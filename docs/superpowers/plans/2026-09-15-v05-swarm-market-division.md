# ZERO Engine v0.5 Swarm Market Division Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ZERO's single narrow sequential scan with one CEO supervisor coordinating four managers and 20 concurrent route-scanning workers, all reading a single pinned Arbitrum block and admitting any candidate whose modeled net profit remains positive after costs and reserve.

**Architecture:** Add block-tagged RPC/state reads, an isolated swarm module for route identity/leasing/opportunity aggregation, configuration-driven manager/worker assignments, and a supervisor that runs bounded concurrent workers against the existing two-pool Uniswap V3 scanner. Keep execution fork-only and reuse the v0.4 candidate/calldata bridge.

**Tech Stack:** Python 3 standard library (`concurrent.futures`, `threading`, `dataclasses`), SQLite ledger, JSON-RPC over HTTP, existing Uniswap V3 math, Foundry regression tests.

**Spec:** `docs/superpowers/specs/2026-09-15-v05-swarm-market-division-design.md`

## Global Constraints

- This slice remains shadow/fork-verification only; no production mainnet signer or broadcaster.
- Exactly 20 scanner workers and four managers are created from configuration by default.
- Every candidate in one scan cycle must derive from the same explicit pinned block.
- Canonical route leases prevent two workers from scanning the same route for the same block.
- `expected_net_profit > 0` is the admission rule in swarm mode; the existing `$2` floor, `4x gas`, and percentage ROI gate must not reject tiny positive-net shadow/fork candidates.
- Swap fees and modeled price impact remain embedded in round-trip quote output and must not be double-counted.
- RPC concurrency is bounded and configurable.
- Worker exceptions are isolated and recorded; one worker failure must not abort the cycle.
- Existing v0.4 fork executor and calldata path remain simulation-only.

---

### Task 1: Add explicit block-tagged RPC and pool state reads

**Files:**
- Modify: `zero/rpc.py`
- Modify: `zero/uniswap_v3.py`
- Modify: `zero/aave.py`
- Test: `tests/test_rpc_block_tags.py`

**Interfaces:**
- Produces: `Rpc.eth_call(to: str, data: str, block: int | str = "latest") -> bytes`
- Produces: `Rpc.get_code(address: str, block: int | str = "latest") -> str`
- Produces: `UniswapV3Pool.fetch_state(block: int | str = "latest") -> dict`
- Aave read methods that use `eth_call` accept and propagate an optional `block` argument where required by the swarm scanner.

- [ ] **Step 1: Write failing block-tag tests**

Create `tests/test_rpc_block_tags.py` with an injectable transport that captures JSON-RPC payloads. Assert:

```python
rpc.eth_call("0x" + "11" * 20, "0x1234", block=123)
assert captured["params"][1] == "0x7b"
```

Also assert `block="latest"` remains backward compatible and `UniswapV3Pool.fetch_state(block=123)` sends both `slot0()` and `liquidity()` reads with the same tag.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest tests.test_rpc_block_tags -v
```

Expected: FAIL because `eth_call`, `get_code`, and `fetch_state` do not yet accept a block argument.

- [ ] **Step 3: Implement one block-tag normalizer**

Add to `zero/rpc.py`:

```python
def _block_tag(block: int | str) -> str:
    if isinstance(block, int):
        if block < 0:
            raise ValueError("block must be non-negative")
        return hex(block)
    if block in {"latest", "pending", "safe", "finalized", "earliest"}:
        return block
    if isinstance(block, str) and block.startswith("0x"):
        return block
    raise ValueError(f"invalid block tag: {block}")
```

Use it in `eth_call` and `get_code`.

- [ ] **Step 4: Propagate the block through Uniswap/Aave read paths used by the scanner**

Change `UniswapV3Pool.fetch_state()` to call:

```python
slot0 = self.rpc.eth_call(self.address, _sel("slot0()"), block=block)
liq = self.rpc.eth_call(self.address, _sel("liquidity()"), block=block)
```

Update Aave scanner reads so pool/oracle/premium/price calls can be pinned to the same block without breaking existing callers.

- [ ] **Step 5: Run focused and existing RPC/Aave/Uniswap tests**

```bash
python3 -m unittest tests.test_rpc_block_tags tests.test_rpc_aave tests.test_uniswap -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add zero/rpc.py zero/uniswap_v3.py zero/aave.py tests/test_rpc_block_tags.py
git commit -m "feat: pin ZERO state reads to explicit blocks"
```

---

### Task 2: Add canonical route IDs and per-block route leasing

**Files:**
- Create: `zero/swarm.py`
- Test: `tests/test_swarm_routes.py`

**Interfaces:**
- Produces: `RouteKey(chain_id, base, quote, pool_a, pool_b, fee_a, fee_b, direction)`
- Produces: `RouteKey.id -> str`
- Produces: `RouteLeaseRegistry.claim(block: int, route_id: str, worker_id: str) -> bool`
- Produces: `RouteLeaseRegistry.release(block: int, route_id: str, worker_id: str) -> None`
- Produces: `RouteLeaseRegistry.expire_before(block: int) -> None`

- [ ] **Step 1: Write failing canonicalization and lease tests**

Assert addresses are lower-cased before hashing/serialization, fee/direction differences produce different IDs, and:

```python
assert leases.claim(100, route.id, "A1") is True
assert leases.claim(100, route.id, "A2") is False
assert leases.claim(101, route.id, "A2") is True
```

- [ ] **Step 2: Run test and verify RED**

```bash
python3 -m unittest tests.test_swarm_routes -v
```

- [ ] **Step 3: Implement `RouteKey` and `RouteLeaseRegistry`**

Use `@dataclass(frozen=True)` for `RouteKey`; construct `id` from a stable pipe-delimited canonical representation hashed with existing `keccak256`.

Use a `threading.Lock` around a dictionary keyed by `(block, route_id)` for leases. Reject release attempts by non-owners with `ValueError` rather than silently corrupting ownership.

- [ ] **Step 4: Run the tests**

```bash
python3 -m unittest tests.test_swarm_routes -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zero/swarm.py tests/test_swarm_routes.py
git commit -m "feat: add canonical swarm route leasing"
```

---

### Task 3: Add the shared opportunity book and positive-net swarm economics

**Files:**
- Modify: `zero/swarm.py`
- Test: `tests/test_swarm_opportunity_book.py`
- Modify: `config/arbitrum.json`

**Interfaces:**
- Produces: `SwarmCandidate` dataclass with candidate/route/worker/manager IDs, block, economics, timestamp, and optional v0.4 candidate payload.
- Produces: `OpportunityBook.add(candidate: SwarmCandidate) -> bool`
- Produces: `OpportunityBook.ranked() -> list[SwarmCandidate]`
- Produces: `swarm_expected_net(gross: float, flash_fee: float, gas: float, model_reserve: float) -> float`

- [ ] **Step 1: Write failing economics/book tests**

Cover these exact boundaries:

```python
assert swarm_expected_net(1.00, 0.40, 0.50, 0.09) == 0.01
assert swarm_expected_net(1.00, 0.40, 0.50, 0.10) == 0.0
```

Assert the book accepts the first candidate for a `(block, route_id)` identity, suppresses duplicates, and ranks primarily by `expected_net` descending.

- [ ] **Step 2: Run RED**

```bash
python3 -m unittest tests.test_swarm_opportunity_book -v
```

- [ ] **Step 3: Implement economics and book**

Use `Decimal(str(value))` for the four-term net subtraction to avoid a tiny positive result becoming negative from binary float noise, then convert to float for existing serialization.

Admission rule in `OpportunityBook.add`:

```python
if candidate.expected_net <= 0:
    return False
```

- [ ] **Step 4: Add swarm configuration**

Add a `swarm` object to `config/arbitrum.json` with:

```json
{
  "enabled": true,
  "worker_count": 20,
  "manager_count": 4,
  "max_rpc_concurrency": 20,
  "model_reserve_usd": 0.0,
  "work_stealing": true,
  "block_driven": true
}
```

Add default manager/worker IDs and primary pair assignments from the approved spec as configuration data, not hard-coded branching logic.

- [ ] **Step 5: Run tests**

```bash
python3 -m unittest tests.test_swarm_opportunity_book -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add zero/swarm.py config/arbitrum.json tests/test_swarm_opportunity_book.py
git commit -m "feat: add swarm opportunity book and positive-net gate"
```

---

### Task 4: Create four managers and 20 configuration-driven worker definitions

**Files:**
- Modify: `zero/swarm.py`
- Test: `tests/test_swarm_topology.py`

**Interfaces:**
- Produces: `WorkerSpec(worker_id: str, manager_id: str, primary_pair: tuple[str, str], role: str)`
- Produces: `ManagerSpec(manager_id: str, worker_ids: tuple[str, ...])`
- Produces: `build_topology(config: dict) -> tuple[list[ManagerSpec], list[WorkerSpec]]`

- [ ] **Step 1: Write failing topology tests**

Assert exactly four unique managers and 20 unique workers are built from the shipped config; every manager owns five primary workers; no worker belongs to two managers; the IDs include A1-A5, B1-B5, C1-C5, D1-D5.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_swarm_topology -v
```

- [ ] **Step 3: Implement topology parsing and validation**

Reject configs where `worker_count`/`manager_count` disagree with actual definitions, worker IDs are duplicated, or a manager references a missing worker.

- [ ] **Step 4: Run tests**

```bash
python3 -m unittest tests.test_swarm_topology -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zero/swarm.py tests/test_swarm_topology.py
git commit -m "feat: configure ZERO CEO manager worker topology"
```

---

### Task 5: Refactor the existing arbitrage scan into a pinned-block route scanner

**Files:**
- Modify: `zero/engine.py`
- Modify: `zero/swarm.py`
- Test: `tests/test_swarm_scanner.py`
- Preserve: `tests/test_candidate_engine.py`, `tests/test_v04_engine_economics.py`

**Interfaces:**
- Produces: `ShadowEngine.scan_cycle_config(block: int, cycle_config: dict, *, swarm_mode: bool = False, model_reserve_usd: float = 0.0) -> dict | None`
- Existing `scan_arbitrage(block)` remains available and uses the helper for backward compatibility.
- Swarm workers invoke the helper with `swarm_mode=True`.

- [ ] **Step 1: Write failing tests for exact-block propagation and tiny-positive admission**

Use fakes for RPC/pools and assert every state call receives the same supplied block. Build a candidate with modeled net `0.001` after flash fee/gas/reserve and assert swarm mode admits it while `expected_net <= 0` is rejected.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_swarm_scanner -v
```

- [ ] **Step 3: Extract one-cycle scanning from `scan_arbitrage`**

Move the per-cycle body into `scan_cycle_config` without changing v0.4 default semantics. In swarm mode, calculate:

```python
expected_net = swarm_expected_net(
    gross=best["gross"],
    flash_fee=flash_fee,
    gas=gas_usd,
    model_reserve=model_reserve_usd,
)
```

and require `expected_net > 0` instead of calling the legacy Gate minimum-profit thresholds.

- [ ] **Step 4: Ensure pool/Aave/oracle reads are pinned to `block`**

Use the interfaces from Task 1 for pool state, pool resolution/premium, and oracle price reads used by this scan.

- [ ] **Step 5: Run focused plus v0.4 regression tests**

```bash
python3 -m unittest tests.test_swarm_scanner tests.test_candidate_engine tests.test_v04_engine_economics -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add zero/engine.py zero/swarm.py tests/test_swarm_scanner.py
git commit -m "refactor: expose pinned-block swarm route scanning"
```

---

### Task 6: Implement CEO supervisor, manager scheduling, work stealing, and failure isolation

**Files:**
- Modify: `zero/swarm.py`
- Test: `tests/test_swarm_supervisor.py`

**Interfaces:**
- Produces: `SwarmSupervisor(engine, config, ledger)`
- Produces: `SwarmSupervisor.run_block(block: int | None = None) -> dict`
- Produces result keys: `block`, `active_workers`, `routes_scanned`, `worker_failures`, `detected`, `positive_net`, `duplicates_suppressed`, `best_expected_net`, `elapsed_s`, `candidates`.

- [ ] **Step 1: Write failing concurrency/failure tests**

Use an injected worker callable so tests do not hit the network. Assert:
- 20 worker tasks are submitted;
- max concurrent executions never exceeds configured `max_rpc_concurrency`;
- one worker raising `RuntimeError("boom")` increments `worker_failures` but other results survive;
- duplicate route leases are suppressed;
- an idle worker may receive a route from another manager's overflow queue when `work_stealing=true`.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_swarm_supervisor -v
```

- [ ] **Step 3: Implement bounded concurrency**

Use `ThreadPoolExecutor(max_workers=max_rpc_concurrency)` and route leases before submission. Keep manager/worker objects lightweight and configuration-driven.

- [ ] **Step 4: Implement work stealing as route-queue leasing, not worker mutation**

When a worker exhausts its primary queue, it asks the supervisor for the next unleased overflow route. Ownership lasts only for the current block.

- [ ] **Step 5: Return deterministic observability metrics**

Sort output candidates by expected net descending before serialization so repeated tests are stable regardless of thread completion order.

- [ ] **Step 6: Run tests**

```bash
python3 -m unittest tests.test_swarm_supervisor -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add zero/swarm.py tests/test_swarm_supervisor.py
git commit -m "feat: add ZERO swarm supervisor and work stealing"
```

---

### Task 7: Add swarm CLI commands without changing legacy commands

**Files:**
- Modify: `zero/cli.py`
- Test: `tests/test_swarm_cli.py`
- Modify: `README.md`

**Interfaces:**
- Produces CLI: `python3 -m zero.cli swarm-once`
- Produces CLI: `python3 -m zero.cli swarm --interval SECONDS`
- Legacy `scan`, `candidate`, `calldata`, `fork-arb`, and `shadow` remain unchanged.

- [ ] **Step 1: Write failing CLI tests**

Patch the supervisor and assert `swarm-once` emits JSON containing all observability keys; assert continuous `swarm` uses the configured supervisor and handles `KeyboardInterrupt` cleanly.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_swarm_cli -v
```

- [ ] **Step 3: Add supervisor factory and commands**

Use the existing config/ledger construction paths. Do not add signer, wallet, or broadcast options.

- [ ] **Step 4: Document commands and safety boundary**

README must state that swarm mode is live-read + local fork-verification infrastructure only, not a mainnet transaction executor.

- [ ] **Step 5: Run CLI tests and smoke help**

```bash
python3 -m unittest tests.test_swarm_cli -v
bash scripts/cli_test.sh
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add zero/cli.py tests/test_swarm_cli.py README.md
git commit -m "feat: expose ZERO 20-worker swarm CLI"
```

---

### Task 8: Full regression, fork verification, and merge-readiness evidence

**Files:**
- Modify if needed: `.github/workflows/test.yml`
- No production behavior changes in this task unless a failing test identifies a regression.

**Interfaces:**
- Produces merge evidence only.

- [ ] **Step 1: Run all Python/CLI tests**

```bash
bash scripts/cli_test.sh
```

Expected: all legacy tests plus new swarm tests PASS.

- [ ] **Step 2: Build Solidity**

```bash
forge build
```

Expected: build success. Existing lints may remain warnings; no compilation errors.

- [ ] **Step 3: Run real Aave fork smoke**

```bash
bash scripts/fork_test.sh
```

Expected: `testRealAaveFlashLoanRepaysOnArbitrumFork()` PASS.

- [ ] **Step 4: Run deterministic Aave+Uniswap candidate fork smoke**

```bash
bash scripts/candidate_fork_test.sh
```

Expected: `testCandidateRouteRepaysAndProfitsOnRealPools()` PASS.

- [ ] **Step 5: Run one live swarm scan**

```bash
python3 -m zero.cli swarm-once
```

Expected: valid JSON with `active_workers: 20`; zero candidates is acceptable, but mixed-block state or unhandled worker errors are not.

- [ ] **Step 6: Ensure CI includes the new Python test suite**

If `.github/workflows/test.yml` already runs `scripts/cli_test.sh`, no workflow change is needed. Otherwise add the same command used locally.

- [ ] **Step 7: Final commit if CI/readme changes were necessary**

```bash
git add .github/workflows/test.yml README.md
git commit -m "test: verify ZERO swarm regression suite"
```

- [ ] **Step 8: Open PR against `main`**

PR title:

```text
ZERO Engine v0.5: 20-worker pinned-block swarm foundation
```

PR body must include the exact test/fork commands and observed PASS results. Do not merge until required checks are green.
