# ZERO Engine v0.5 Swarm Market Division Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ZERO's single narrow sequential scan with one CEO supervisor coordinating four managers and 20 concurrent route-scanning workers, all reading a single pinned Arbitrum block and admitting any candidate whose modeled net profit remains positive after costs and reserve.

**Architecture:** Add block-tagged RPC/state reads, configuration-driven market topology, dynamic Uniswap V3 route discovery, canonical route leasing, one per-block scan context/cache, a shared opportunity book, bounded concurrent workers, and CEO-owned serialized ledger/fork verification. Keep execution fork-only and reuse the v0.4 two-pool candidate/calldata bridge.

**Tech Stack:** Python 3 standard library (`concurrent.futures`, `threading`, `dataclasses`, `decimal`), SQLite ledger, JSON-RPC over HTTP, existing Uniswap V3 math, Foundry regression tests.

**Spec:** `docs/superpowers/specs/2026-09-15-v05-swarm-market-division-design.md`

## Global Constraints

- This slice remains shadow/fork-verification only; no production mainnet signer or broadcaster.
- Exactly 20 scanner workers and four managers are created from configuration by default.
- Every candidate in one scan cycle must derive from one explicit pinned Arbitrum block.
- Canonical route leases prevent two workers from scanning the same route for the same block.
- `expected_net_profit > 0` is the swarm admission rule; the existing `$2` floor, `4x gas`, and percentage ROI gate must not reject tiny positive-net shadow/fork candidates.
- Swap fees and modeled price impact are already embedded in round-trip quote output and must not be double-counted.
- RPC concurrency is bounded and configurable.
- Worker exceptions are isolated and recorded; one worker failure must not abort the cycle.
- Workers never write SQLite directly; the CEO serializes all ledger writes after worker results return.
- Existing `ZeroForkExecutor` and calldata path remain simulation-only.
- v0.5 swarm execution remains two-pool Uniswap V3 only; multi-DEX and multi-hop are subsequent slices.

---

### Task 1: Add explicit block-tagged RPC and state reads

**Files:**
- Modify: `zero/rpc.py`
- Modify: `zero/uniswap_v3.py`
- Modify: `zero/aave.py`
- Test: `tests/test_rpc_block_tags.py`

**Interfaces:**
- Produces: `Rpc.eth_call(to: str, data: str, block: int | str = "latest") -> bytes`
- Produces: `Rpc.get_code(address: str, block: int | str = "latest") -> str`
- Produces: `UniswapV3Pool.fetch_state(block: int | str = "latest") -> dict`
- Aave read methods used by swarm accept/propagate an optional `block` argument.

- [ ] **Step 1: Write failing block-tag tests**

Create `tests/test_rpc_block_tags.py` with an injectable transport that captures JSON-RPC payloads. Assert:

```python
rpc.eth_call("0x" + "11" * 20, "0x1234", block=123)
assert captured["params"][1] == "0x7b"
```

Also assert `block="latest"` remains backward compatible and `UniswapV3Pool.fetch_state(block=123)` sends both `slot0()` and `liquidity()` reads with `0x7b`.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m unittest tests.test_rpc_block_tags -v
```

Expected: FAIL because current wrappers always use `latest`.

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

- [ ] **Step 4: Propagate the block through Uniswap and Aave scanner reads**

Change `UniswapV3Pool.fetch_state()` to:

```python
slot0 = self.rpc.eth_call(self.address, _sel("slot0()"), block=block)
liq = self.rpc.eth_call(self.address, _sel("liquidity()"), block=block)
```

Update Aave pool/oracle/premium/price/reserve reads used by the swarm so a caller can pin them to the same block.

- [ ] **Step 5: Run focused plus legacy read tests**

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

Assert addresses are lower-cased before identity construction, fee/direction differences produce different IDs, and:

```python
assert leases.claim(100, route.id, "A1") is True
assert leases.claim(100, route.id, "A2") is False
assert leases.claim(101, route.id, "A2") is True
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_swarm_routes -v
```

- [ ] **Step 3: Implement `RouteKey` and `RouteLeaseRegistry`**

Use `@dataclass(frozen=True)` for `RouteKey`. Create the ID from a stable pipe-delimited canonical string hashed with the repository's existing `keccak256`. Protect the lease dictionary with `threading.Lock`.

- [ ] **Step 4: Run tests**

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

### Task 3: Add swarm economics, opportunity book, and configuration

**Files:**
- Modify: `zero/swarm.py`
- Modify: `config/arbitrum.json`
- Test: `tests/test_swarm_opportunity_book.py`

**Interfaces:**
- Produces: `SwarmCandidate` with candidate/route/worker/manager IDs, block, economics, timestamp, and optional v0.4 candidate payload.
- Produces: `OpportunityBook.add(candidate: SwarmCandidate) -> bool`
- Produces: `OpportunityBook.ranked() -> list[SwarmCandidate]`
- Produces: `swarm_expected_net(gross: float, flash_fee: float, gas: float, model_reserve: float) -> float`

- [ ] **Step 1: Write failing economics/book tests**

Cover exact boundaries:

```python
assert swarm_expected_net(1.00, 0.40, 0.50, 0.09) == 0.01
assert swarm_expected_net(1.00, 0.40, 0.50, 0.10) == 0.0
```

Assert one candidate per `(block, route_id)` is retained and ranking is expected-net descending.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_swarm_opportunity_book -v
```

- [ ] **Step 3: Implement economics and book**

Use `Decimal(str(value))` for the four-term subtraction. Admission rule:

```python
if candidate.expected_net <= 0:
    return False
```

- [ ] **Step 4: Add swarm configuration**

Add:

```json
{
  "swarm": {
    "enabled": true,
    "worker_count": 20,
    "manager_count": 4,
    "max_rpc_concurrency": 20,
    "model_reserve_usd": 0.0,
    "size_ladder_usd": [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000],
    "fee_tiers": [100, 500, 3000, 10000],
    "work_stealing": true,
    "block_driven": true,
    "verify_positive_candidates": true,
    "max_fork_concurrency": 1
  }
}
```

Add manager/worker IDs and primary symbol pairs from the approved spec as configuration data, not branching logic.

- [ ] **Step 5: Run tests**

```bash
python3 -m unittest tests.test_swarm_opportunity_book -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add zero/swarm.py config/arbitrum.json tests/test_swarm_opportunity_book.py
git commit -m "feat: add swarm opportunity book and positive-net economics"
```

---

### Task 4: Build configuration-driven topology, pinned token registry, and Uniswap route catalog

**Files:**
- Modify: `zero/swarm.py`
- Test: `tests/test_swarm_topology.py`
- Test: `tests/test_swarm_discovery.py`

**Interfaces:**
- Produces: `WorkerSpec(worker_id: str, manager_id: str, primary_pair: tuple[str, str], role: str)`
- Produces: `ManagerSpec(manager_id: str, worker_ids: tuple[str, ...])`
- Produces: `build_topology(config: dict) -> tuple[list[ManagerSpec], list[WorkerSpec]]`
- Produces: `TokenInfo(symbol: str, address: str, decimals: int, price_usd: float)`
- Produces: `build_token_registry(aave, block: int) -> dict[str, TokenInfo]`
- Produces: `discover_uniswap_routes(engine, pair: tuple[str, str], registry, block: int, fee_tiers: list[int]) -> list[dict]`

- [ ] **Step 1: Write failing topology tests**

Assert exactly four managers and 20 unique workers are built; every manager owns five; IDs include A1-A5, B1-B5, C1-C5, D1-D5.

- [ ] **Step 2: Write failing token/route discovery tests**

Use fake Aave reserve metadata for `USDC`, `WETH`, `WBTC`, `tBTC`, `wstETH`, `rETH`, `weETH`, `ezETH`, `rsETH`, `ARB`, `LINK`, `AAVE`, and `GHO` so worker assignments resolve by symbol without hard-coded token business logic.

Mock Uniswap factory `getPool` at fee tiers `[100, 500, 3000, 10000]`; return zero address for missing pools. Assert only existing pools are retained, all factory calls use the pinned block, and every two-distinct-pool ordering yields a valid two-hop route candidate.

- [ ] **Step 3: Verify RED**

```bash
python3 -m unittest tests.test_swarm_topology tests.test_swarm_discovery -v
```

- [ ] **Step 4: Implement topology validation**

Reject duplicate workers, missing manager references, or declared counts that disagree with the actual configuration.

- [ ] **Step 5: Implement token registry from pinned Aave reserves**

Resolve symbols/decimals/prices once per block. Do not let each worker rediscover the same metadata independently.

- [ ] **Step 6: Implement fee-tier pool discovery and route generation**

For each configured pair, query the existing Uniswap V3 factory at the pinned block, discard the zero address, and build ordered distinct-pool route pairs. Preserve base/quote decimals in each route config so the existing `Cycle` math can be reused.

- [ ] **Step 7: Run tests**

```bash
python3 -m unittest tests.test_swarm_topology tests.test_swarm_discovery -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add zero/swarm.py tests/test_swarm_topology.py tests/test_swarm_discovery.py
git commit -m "feat: discover ZERO swarm token and route catalog"
```

---

### Task 5: Add one immutable per-block scan context and pinned route scanner

**Files:**
- Modify: `zero/engine.py`
- Modify: `zero/swarm.py`
- Test: `tests/test_swarm_scan_context.py`
- Test: `tests/test_swarm_scanner.py`
- Preserve: `tests/test_candidate_engine.py`, `tests/test_v04_engine_economics.py`

**Interfaces:**
- Produces: `ScanContext(block, aave_pool, oracle, premium_bps, eth_price_usd, gas_usd, tokens, pool_states)`
- Produces: `build_scan_context(engine, block: int, route_catalog: list[dict]) -> ScanContext`
- Produces: `ShadowEngine.scan_cycle_config(block: int, cycle_config: dict, *, sizes: list[float] | None = None, swarm_mode: bool = False, model_reserve_usd: float = 0.0, context: ScanContext | None = None) -> dict | None`

- [ ] **Step 1: Write failing context tests**

Assert Aave pool/oracle/premium/gas inputs and every pool state are captured once at the same block. Assert a context for block `N` cannot be used to scan a route declared for block `N+1`.

- [ ] **Step 2: Write failing tiny-positive and sizing tests**

Use `size_ladder_usd` and token oracle price to convert USD notionals into base-token amounts:

```python
base_amount = usd_size / base_price_usd
```

Assert the scanner chooses the amount with highest expected net, not highest loan size. Assert modeled net `0.001` is admitted in swarm mode while zero/negative net is rejected.

- [ ] **Step 3: Verify RED**

```bash
python3 -m unittest tests.test_swarm_scan_context tests.test_swarm_scanner -v
```

- [ ] **Step 4: Extract one-cycle scanning from legacy `scan_arbitrage`**

Keep existing v0.4 behavior as default. In swarm mode use the supplied context and `swarm_expected_net` instead of the legacy Gate floor.

- [ ] **Step 5: Enforce context block equality**

Raise `ValueError("mixed-block scan context")` if candidate/route/context block identities disagree.

- [ ] **Step 6: Run focused and v0.4 regressions**

```bash
python3 -m unittest tests.test_swarm_scan_context tests.test_swarm_scanner tests.test_candidate_engine tests.test_v04_engine_economics -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add zero/engine.py zero/swarm.py tests/test_swarm_scan_context.py tests/test_swarm_scanner.py
git commit -m "refactor: add pinned-block swarm scan context"
```

---

### Task 6: Implement CEO supervisor, managers, work stealing, and serialized ledger writes

**Files:**
- Modify: `zero/swarm.py`
- Test: `tests/test_swarm_supervisor.py`
- Test: `tests/test_swarm_ledger.py`

**Interfaces:**
- Produces: `SwarmSupervisor(engine, config, ledger, verifier=None)`
- Produces: `SwarmSupervisor.run_block(block: int | None = None) -> dict`
- Result keys: `block`, `active_workers`, `routes_scanned`, `worker_failures`, `detected`, `positive_net`, `duplicates_suppressed`, `best_expected_net`, `fork_verifications_attempted`, `fork_verifications_passed`, `fork_verifications_failed`, `elapsed_s`, `candidates`.

- [ ] **Step 1: Write failing concurrency/failure tests**

Inject a worker callable so tests avoid network access. Assert 20 worker tasks are created, max in-flight work respects `max_rpc_concurrency`, one worker throwing `RuntimeError("boom")` does not abort other results, duplicate routes are suppressed, and an idle worker can lease overflow work when work stealing is enabled.

- [ ] **Step 2: Write failing serialized-ledger test**

Use a fake ledger that records the current thread name on every `record()` call. Assert worker callables never invoke the ledger and all ledger writes happen after future collection on the supervisor thread.

- [ ] **Step 3: Verify RED**

```bash
python3 -m unittest tests.test_swarm_supervisor tests.test_swarm_ledger -v
```

- [ ] **Step 4: Implement bounded worker concurrency**

Use `ThreadPoolExecutor(max_workers=max_rpc_concurrency)`. Acquire route lease before submission and release/expire it deterministically at block completion.

- [ ] **Step 5: Implement work stealing as route-queue leasing**

Primary queues are assigned first. When exhausted, a worker requests the next unleased overflow route; manager identity remains the worker's configured manager, while route ownership is the lease.

- [ ] **Step 6: Aggregate deterministically and serialize ledger writes**

Sort worker results by route ID before ledger persistence and expected net descending for opportunity output. Record worker errors as CEO-owned ledger/detail events; never use one SQLite connection concurrently across worker threads.

- [ ] **Step 7: Run tests**

```bash
python3 -m unittest tests.test_swarm_supervisor tests.test_swarm_ledger -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add zero/swarm.py tests/test_swarm_supervisor.py tests/test_swarm_ledger.py
git commit -m "feat: add ZERO CEO manager worker scheduler"
```

---

### Task 7: Dispatch positive candidates to the existing exact-block fork verifier and expose metrics

**Files:**
- Modify: `zero/swarm.py`
- Modify: `zero/cli.py`
- Test: `tests/test_swarm_verifier.py`

**Interfaces:**
- Consumes existing: `build_candidate_calldata(result: dict, cfg: dict) -> dict | None`
- Consumes existing: `run_live_candidate_fork(upstream_rpc: str, payload: dict) -> int`
- Produces supervisor verifier hook: `verifier(candidate_payload: dict) -> int`

- [ ] **Step 1: Write failing verifier tests**

Inject a fake verifier returning `0`, `1`, and raising an exception. Assert only `expected_net > 0` candidates are submitted; duplicate `(block, route_id)` candidates are submitted once; attempted/passed/failed counters are correct; verifier failure does not kill the scan result.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_swarm_verifier -v
```

- [ ] **Step 3: Add a bounded verifier phase after worker aggregation**

Build v0.4 calldata only for compatible two-pool candidates. Run exact-block fork verification with `max_fork_concurrency` from config; default `1` because Foundry fork runs are resource-heavy.

- [ ] **Step 4: Do not parse realized P&L in this slice**

Record only verifier exit status plus candidate metadata. Structured realized P&L is the immediately following v0.5.1 subsystem and must not be faked from predicted values.

- [ ] **Step 5: Run tests**

```bash
python3 -m unittest tests.test_swarm_verifier -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add zero/swarm.py zero/cli.py tests/test_swarm_verifier.py
git commit -m "feat: fork-verify positive ZERO swarm candidates"
```

---

### Task 8: Add swarm CLI commands and observability

**Files:**
- Modify: `zero/cli.py`
- Test: `tests/test_swarm_cli.py`
- Modify: `README.md`

**Interfaces:**
- Produces CLI: `python3 -m zero.cli swarm-once`
- Produces CLI: `python3 -m zero.cli swarm --interval SECONDS`
- Legacy `scan`, `candidate`, `calldata`, `fork-arb`, and `shadow` remain available.

- [ ] **Step 1: Write failing CLI tests**

Patch the supervisor and assert `swarm-once` emits JSON containing pinned block, active workers, routes scanned, failures, detected/positive counts, duplicate suppression, best expected net, fork counters, and elapsed time.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.test_swarm_cli -v
```

- [ ] **Step 3: Add supervisor construction and commands**

Use existing config/ledger paths. `swarm` loops until interrupted; `swarm-once` runs exactly one pinned block. Neither command exposes signer, wallet, or mainnet broadcast options.

- [ ] **Step 4: Document the runtime and safety boundary**

README must state that the swarm is live-read + local exact-block fork verification only.

- [ ] **Step 5: Run CLI tests and help smoke**

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

### Task 9: Full regression, fork verification, and merge-readiness evidence

**Files:**
- Modify if required: `.github/workflows/test.yml`
- No production behavior changes unless verification finds a regression.

**Interfaces:**
- Produces merge evidence only.

- [ ] **Step 1: Run all Python/CLI tests**

```bash
bash scripts/cli_test.sh
```

Expected: all legacy and new swarm tests PASS.

- [ ] **Step 2: Build Solidity**

```bash
forge build
```

Expected: build success; existing lints may remain warnings but no compile errors.

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

- [ ] **Step 5: Run one live swarm cycle**

```bash
python3 -m zero.cli swarm-once
```

Expected: valid output with `active_workers: 20`; zero positive candidates is acceptable. Mixed-block data, unhandled worker errors, duplicate route ownership, or SQLite thread errors are not.

- [ ] **Step 6: Ensure CI executes the new Python suite**

If `.github/workflows/test.yml` already calls `scripts/cli_test.sh`, leave it unchanged. Otherwise add that exact command.

- [ ] **Step 7: Commit any verification-only adjustments**

```bash
git add .github/workflows/test.yml README.md
git commit -m "test: verify ZERO swarm regression suite"
```

- [ ] **Step 8: Open PR against `main`**

PR title:

```text
ZERO Engine v0.5: 20-worker pinned-block swarm foundation
```

PR body must list the exact Python/Foundry/fork commands and observed results. Do not merge until required checks are green.
