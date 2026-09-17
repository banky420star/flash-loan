# ZERO Live Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend ZERO from shadow/fork verification into a guarded Arbitrum controller with paper mode, explicit live arming, secure keystore signing, nonce-safe submission, receipt reconciliation, and clearly separated live realized P&L.

**Architecture:** Preserve the existing scanner and fork verifier. Add a separate controller that consumes new Arbitrum heads, runs the executable-only scanner at a pinned block, applies a production profit gate plus the existing `ExecutionPolicy`, performs an `eth_call` preflight against `ZeroExecutor`, and only then routes an authorized request to an isolated signer/broadcaster. Persist pending transactions before broadcast completion and reconcile receipts into a dedicated live ledger before updating risk state or TUI telemetry.

**Tech Stack:** Python 3.13, standard library, `websockets==15.0.1`, Foundry `cast`/`forge`, SQLite WAL ledger, Solidity 0.8.24, curses TUI.

**Spec:** `docs/superpowers/specs/2026-09-17-zero-live-production-design.md`

## Global Constraints

- Arbitrum One only: chain ID `42161`.
- Modes are exactly `shadow`, `paper`, and `live`; default is `shadow`.
- `live` always starts DISARMED unless an explicit persistent-arm option is later configured; this slice does not auto-arm.
- No raw private key or mnemonic is stored in repository files or `.env`; first signer backend is a Foundry encrypted keystore plus password-file reference.
- First live route scope is Uniswap V3 two-pool arbitrage only; Sushi V3 and Camelot stay observation-only.
- Live submission requires exact preflight, production profit gate, `ExecutionPolicy`, duplicate suppression, and ARM state.
- Initial live rollout allows at most one pending transaction.
- Live P&L means mined on-chain executor profit minus actual transaction gas; fork P&L remains simulated and separate.
- Any hard risk-limit breach disarms new submissions and surfaces the reason in runtime/TUI state.
- All new behavior follows TDD: failing test first, then minimal implementation, then full regression verification.

---

## File Map

- Create `requirements.txt`: pin `websockets==15.0.1`.
- Create `zero/control.py`: atomic ARM/DISARM/KILL control state.
- Create `zero/live_gate.py`: production profit-floor calculation.
- Create `zero/head_stream.py`: WebSocket `newHeads` source and coalescing scheduler.
- Create `zero/live_tx.py`: executor call model, ABI encoding, preflight request.
- Create `zero/signer.py`: Foundry encrypted-keystore signer boundary.
- Create `zero/nonce.py`: serialized nonce reservation/restart reconciliation.
- Create `zero/broadcast.py`: raw transaction broadcast and replacement classification.
- Create `zero/reconcile.py`: receipt/event parsing and realized live P&L.
- Create `zero/controller.py`: paper/live orchestration state machine.
- Create `zero/live_doctor.py`: read-only production readiness checks.
- Modify `zero/rpc.py`: pending nonce, `eth_call` with sender, receipt, transaction, fee helpers.
- Modify `zero/ledger.py`: `live_transactions` and live-economics audit schema.
- Modify `zero/execution_policy.py`: pending-count and live-loss inputs without adding network code.
- Modify `zero/cli.py`: `live-doctor`, `controller`, `arm`, `disarm`, `kill`, `reconcile`.
- Modify `zero/runtime.py`: live mode/arm/tx telemetry fields.
- Modify `zero/tui_data.py` and `zero/tui.py`: separate wallet P&L and simulated P&L.
- Modify `config/arbitrum.json` and `.env.example`: safe live defaults and non-secret references.
- Modify `README.md`; create `docs/runbooks/zero-live.md`: deployment/operation runbook.
- Create `scripts/zero_controller_tui.sh`: non-arming controller/TUI launcher.
- Add focused tests under `tests/test_live_*.py`, plus updates to existing CLI/runtime/TUI safety tests.

### Task 0: Preserve the verified scanner/TUI baseline

**Files:**
- Existing modified files: `config/arbitrum.json`, `zero/pnl.py`, `zero/runtime.py`, `zero/swarm_batch.py`, `zero/tui.py`, and their current tests.
- Do not add: `zero_ledger.db-shm`, `zero_ledger.db-wal`.

**Interfaces:**
- Consumes: current executable-only/batched scanner and colored TUI changes already present in the working tree.
- Produces: one clean prerequisite commit from which live work can be isolated in a worktree.

- [ ] **Step 1: Run the current baseline suite**

Run: `bash scripts/cli_test.sh`
Expected: `Ran 250 tests` followed by `OK` and `ZERO CLI TESTS: PASS`.

- [ ] **Step 2: Re-run the live scanner smoke without any live execution**

Run: `python3 -m zero.cli swarm-once --ledger run/baseline-ledger.db`
Expected: JSON with `worker_failures: 0`, timing fields present, and no signing/broadcast activity.

- [ ] **Step 3: Commit only the verified baseline files**

```bash
git add config/arbitrum.json zero/pnl.py zero/runtime.py zero/swarm_batch.py zero/tui.py \
  tests/test_multihop_catalog.py tests/test_runtime_progress.py \
  tests/test_swarm_batched_discovery.py tests/test_swarm_batched_registry.py \
  tests/test_swarm_timings.py tests/test_tui_render.py
git commit -m "perf: tighten ZERO scanner and monitoring"
```
### Task 1: Operating modes, control store, and production profit gate

**Files:**
- Create: `zero/control.py`
- Create: `zero/live_gate.py`
- Modify: `config/arbitrum.json`
- Modify: `.env.example`
- Test: `tests/test_live_control.py`
- Test: `tests/test_live_gate.py`

**Interfaces:**
- Produces: `ControlState`, `ControlStore`, `required_live_net_usd()`, `evaluate_live_profit()`.
- Later tasks consume these interfaces; no signer/network code is allowed here.

```python
@dataclass(frozen=True)
class ControlState:
    mode: str = "shadow"
    armed: bool = False
    killed: bool = False
    kill_reason: str | None = None
    generation: int = 0
    updated_at: float = 0.0

class ControlStore:
    def read(self) -> ControlState: ...
    def arm(self, *, mode: str, now: float) -> ControlState: ...
    def disarm(self, *, now: float) -> ControlState: ...
    def kill(self, reason: str, *, now: float) -> ControlState: ...
    def clear_kill(self, *, now: float) -> ControlState: ...
```
- [ ] **Step 1: Write failing control-state tests**

```python
def test_live_starts_disarmed_and_kill_persists(tmp_path):
    store = ControlStore(tmp_path / "control.json")
    assert store.read().armed is False
    armed = store.arm(mode="live", now=100.0)
    assert armed.armed is True
    killed = store.kill("operator", now=101.0)
    assert killed.killed is True and killed.armed is False
    assert ControlStore(tmp_path / "control.json").read().killed is True
```

Run: `python3 -m unittest tests.test_live_control -v`
Expected: FAIL because `zero.control` does not exist.

- [ ] **Step 2: Implement atomic control writes**

Use `tempfile.mkstemp`, `os.fsync`, `os.replace`, and `chmod 0o600`. Reject any mode outside `shadow|paper|live`. `arm()` must reject `shadow`; `kill()` must always force `armed=False`.

- [ ] **Step 3: Write failing profit-gate tests**

```python
def test_required_live_net_uses_strictest_floor():
    assert required_live_net_usd(
        configured_min_live_profit_usd=2.0, gas_usd=1.0,
        min_profit_to_gas_multiple=4.0, adaptive_reserve_usd=3.0,
        execution_buffer_usd=2.0) == 5.0
```

Run: `python3 -m unittest tests.test_live_gate -v`
Expected: FAIL because `zero.live_gate` does not exist.
- [ ] **Step 4: Implement the production profit gate**

```python
def required_live_net_usd(*, configured_min_live_profit_usd: float,
                          gas_usd: float,
                          min_profit_to_gas_multiple: float,
                          adaptive_reserve_usd: float,
                          execution_buffer_usd: float) -> float:
    return max(
        float(configured_min_live_profit_usd),
        float(gas_usd) * float(min_profit_to_gas_multiple),
        float(adaptive_reserve_usd) + float(execution_buffer_usd),
    )
```

`evaluate_live_profit(expected_net_usd, required_net_usd)` returns `PolicyDecision(True, "ok")` only when `expected_net_usd >= required_net_usd`; otherwise `live_profit_floor`.

- [ ] **Step 5: Add safe configuration defaults**

Add under `live` in `config/arbitrum.json`:

```json
{"arm_required":true,"allowed_tokens":["USDC","WETH"],"loan_assets":["USDC"],
 "min_live_profit_usd":2.0,"min_profit_to_gas_multiple":4.0,
 "execution_buffer_usd":0.50,"max_block_lag":1,"max_gas_usd":5.0,
 "max_loan_notional_usd":1000.0,"max_consecutive_reverts":2,
 "max_daily_loss_usd":10.0,"max_pending_transactions":1,
 "candidate_ttl_s":2.0,"receipt_timeout_s":30.0,
 "max_replacement_attempts":1,"control_path":"run/zero-control.json"}
```

Add non-secret names to `.env.example`: `ZERO_MODE=shadow`, `ZERO_LIVE_ARM_REQUIRED=1`, `ZERO_WS_URL=`, `ZERO_BROADCAST_RPC_URL=`, `ZERO_EXECUTOR_ADDRESS=`, `ZERO_EXECUTOR_CODE_HASH=`, `ZERO_SIGNER_ADDRESS=`, `ZERO_SIGNER_BACKEND=foundry-keystore`, `ZERO_KEYSTORE_PATH=`, `ZERO_KEYSTORE_PASSWORD_FILE=`.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m unittest tests.test_live_control tests.test_live_gate -v`
Expected: PASS.

```bash
git add zero/control.py zero/live_gate.py config/arbitrum.json .env.example \
  tests/test_live_control.py tests/test_live_gate.py
git commit -m "feat: add ZERO live control and profit gate"
```
### Task 2: WebSocket new-head ingestion with stale-work coalescing

**Files:**
- Create: `requirements.txt`
- Create: `zero/head_stream.py`
- Test: `tests/test_live_head_stream.py`

**Interfaces:**
- Produces: `HeadEvent(number: int, hash: str, timestamp: int | None)`, `parse_new_head()`, `WebSocketHeadSource`, `LatestHeadQueue`.
- `LatestHeadQueue` exposes only the newest unprocessed block so scans cannot backlog.

```python
@dataclass(frozen=True)
class HeadEvent:
    number: int
    hash: str
    timestamp: int | None = None

class LatestHeadQueue:
    async def put(self, head: HeadEvent) -> None: ...
    async def get(self) -> HeadEvent: ...
```

- [ ] **Step 1: Pin the WebSocket dependency and write failing parser/coalescing tests**

`requirements.txt` contains exactly:

```text
websockets==15.0.1
```

```python
def test_parse_new_head_hex_number():
    msg={"params":{"result":{"number":"0x64","hash":"0xabc","timestamp":"0x3e8"}}}
    assert parse_new_head(msg) == HeadEvent(100,"0xabc",1000)

async def test_latest_head_queue_drops_intermediate_heads():
    q=LatestHeadQueue(); await q.put(HeadEvent(100,"a")); await q.put(HeadEvent(101,"b"))
    assert (await q.get()).number == 101
```

Run: `python3 -m unittest tests.test_live_head_stream -v`
Expected: FAIL because module is missing.
- [ ] **Step 2: Implement `WebSocketHeadSource`**

Use `from websockets.asyncio.client import connect`. On connection, send:

```json
{"jsonrpc":"2.0","id":1,"method":"eth_subscribe","params":["newHeads"]}
```

Require a successful subscription response before yielding notifications. Decode `number`, `hash`, and optional `timestamp` from hex. On disconnect, reconnect with bounded backoff `0.5, 1, 2, 4, 5` seconds; reset backoff after one valid head. Reject malformed heads rather than synthesizing block numbers.

- [ ] **Step 3: Add a fake-connector test for reconnect behavior**

Inject a connector callable into `WebSocketHeadSource` so tests can provide two fake socket sessions: first disconnects after block 100, second yields block 101. Assert output is `[100, 101]` and no duplicate subscription IDs are accepted as heads.

Run: `python3 -m unittest tests.test_live_head_stream -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt zero/head_stream.py tests/test_live_head_stream.py
git commit -m "feat: add Arbitrum head-driven scan source"
```

### Task 3: RPC primitives, executor call encoding, and exact preflight

**Files:**
- Modify: `zero/rpc.py`
- Create: `zero/live_tx.py`
- Test: `tests/test_live_rpc.py`
- Test: `tests/test_live_tx.py`

**Interfaces:**
- Produces RPC methods `transaction_count(address, block="pending")`, `transaction_receipt(hash)`, `transaction(hash)`, `estimate_gas(tx, block="pending")`, and `eth_call_tx(tx, block="pending")`.
- Produces `ExecutorRunRequest` and `CastExecutorEncoder.encode(request) -> str`.

```python
@dataclass(frozen=True)
class ExecutorStep:
    kind: int; target: str; token_in: str; token_out: str; account: str
    amount: int; limit: int; fee: int; recipient_mode: int

@dataclass(frozen=True)
class ExecutorRunRequest:
    executor: str; asset: str; amount: int; min_profit: int; deadline: int
    steps: tuple[ExecutorStep, ...]
```
- [ ] **Step 1: Write failing RPC tests**

Use the existing injectable transport pattern. Assert exact JSON-RPC methods and tags for `eth_getTransactionCount`, `eth_getTransactionReceipt`, `eth_getTransactionByHash`, `eth_estimateGas`, and sender-aware `eth_call`.

```python
assert rpc.transaction_count(SIGNER, block="pending") == 7
rpc.eth_call_tx({"from":SIGNER,"to":EXECUTOR,"data":"0x1234"}, block="pending")
```

Run: `python3 -m unittest tests.test_live_rpc -v`
Expected: FAIL on missing methods.

- [ ] **Step 2: Add the RPC wrappers without changing existing call semantics**

`transaction_receipt()` and `transaction()` return `None` on JSON-RPC `null`. Numeric hex fields remain raw in the wrapper; reconciliation owns conversion.

- [ ] **Step 3: Write failing executor-call tests**

The live Uniswap path has exactly two `ZeroExecutor.Step` entries, not the legacy approve+swap+swap fork bridge. First swap uses `recipient_mode=1`, second uses `recipient_mode=0` and `amount=0`.

Use the signature:

```text
run(address,uint256,uint256,uint256,(uint8,address,address,address,address,uint256,uint256,uint24,uint8)[])
```

Contract minimum profit is:

```python
contract_min_profit_usd = required_live_net_usd + gas_usd
contract_min_profit_raw = max(
    candidate_min_profit_raw,
    ceil(contract_min_profit_usd / base_price_usd * (10 ** base_decimals)),
)
```

- [ ] **Step 4: Implement `CastExecutorEncoder`**

Centralize `cast_run_args(request)` so both `cast calldata` and the signer use the identical function signature and tuple-array argument. Execute `cast calldata` with `subprocess.run(..., capture_output=True, text=True, check=True)` and require a hex result.

- [ ] **Step 5: Add exact preflight**

`preflight(rpc, request, from_address)` encodes the request, calls `eth_estimateGas` and `eth_call` against `"pending"`, and returns `PreflightResult(ok, gas_limit, data, reason)`. Any revert/RPC error returns `ok=False`; there is no fallback that bypasses preflight.

Run: `python3 -m unittest tests.test_live_rpc tests.test_live_tx -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add zero/rpc.py zero/live_tx.py tests/test_live_rpc.py tests/test_live_tx.py
git commit -m "feat: add executor transaction preflight"
```
### Task 4: Persistent live transaction ledger and nonce manager

**Files:**
- Modify: `zero/ledger.py`
- Create: `zero/nonce.py`
- Test: `tests/test_live_ledger.py`
- Test: `tests/test_live_nonce.py`

**Interfaces:**
- Produces live transaction states `DETECTED`, `PREFLIGHTED`, `AUTHORIZED`, `SIGNED`, `SUBMITTED`, `MINED`, `REVERTED`, `DROPPED`, `REPLACED`, `PAPER`.
- Produces `NonceManager.reserve(signer, chain_pending_nonce) -> int` and `NonceManager.reconcile(signer, chain_pending_nonce) -> NonceReconcileResult`.

Add `live_transactions` with fields: `id`, `candidate_id`, `route_id`, `detected_block`, `state`, `nonce`, `tx_hash`, `replacement_count`, `signer_address`, `executor_address`, `gas_price_wei`, `base_asset`, `base_decimals`, `base_price_usd`, `loan_notional_usd`, `predicted_net_usd`, `required_net_usd`, `gas_estimate`, `submitted_at`, `mined_block`, `gas_used`, `effective_gas_price`, `realized_profit_raw`, `realized_profit_usd`, `gas_cost_usd`, `realized_net_usd`, `model_error_usd`, `error`, `detail`, `created_at`, `updated_at`.

- [ ] **Step 1: Write failing migration/audit tests**

Create an old-format ledger, reopen it, and assert the new table is added without changing fork/opportunity rows. Assert duplicate `(candidate_id, detected_block)` insertion is rejected by a unique index.

- [ ] **Step 2: Implement live ledger methods**

Required methods:

```python
record_live_candidate(...)->int
transition_live_tx(row_id:int, *, expected_state:str, new_state:str, **fields)->None
pending_live_transactions(signer:str|None=None)->list[dict]
live_tx_by_hash(tx_hash:str)->dict|None
live_risk_state(now:float)->dict
live_pnl_summary(session_start:float|None=None, now:float|None=None)->dict
```

`transition_live_tx` must use `UPDATE ... WHERE id=? AND state=?`; if rowcount is not `1`, raise `RuntimeError("invalid live transaction transition")`.

- [ ] **Step 3: Write failing nonce tests**

Test that chain pending nonce `7` plus persisted pending nonce `7` reserves `8`; two concurrent reservation calls cannot return the same nonce; restart reconciliation keeps submitted/mined nonces distinct.

- [ ] **Step 4: Implement `NonceManager`**

Use a process-local `threading.Lock`. Determine next nonce as `max(chain_pending_nonce, max(persisted active nonce)+1)`. Refuse reservation when `pending_count >= max_pending_transactions`. Never decrement or reuse a pending nonce automatically.

Run: `python3 -m unittest tests.test_live_ledger tests.test_live_nonce -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add zero/ledger.py zero/nonce.py tests/test_live_ledger.py tests/test_live_nonce.py
git commit -m "feat: persist live transaction lifecycle"
```
### Task 5: Keystore signer and raw-transaction broadcaster

**Files:**
- Create: `zero/signer.py`
- Create: `zero/broadcast.py`
- Modify: `zero/rpc.py`
- Modify: `tests/test_no_live_broadcast.py`
- Test: `tests/test_live_signer.py`
- Test: `tests/test_live_broadcast.py`

**Interfaces:**
- Produces `FoundryKeystoreSigner.address() -> str` and `sign(request, *, nonce, gas_limit, gas_price_wei) -> str`.
- Produces `Broadcaster.submit(raw_tx) -> str` and `replacement_gas_price(previous, network) -> int`.
- Signer accepts only a keystore path and password-file path; it never accepts raw key text.

- [ ] **Step 1: Replace the old blanket no-broadcast safety test**

Keep assertions that `PRIVATE_KEY`, `ZERO_PRIVATE_KEY`, and mnemonic inputs are absent. Replace the `eth_sendRawTransaction` ban with a structural assertion: the raw-send method may exist only in `zero/broadcast.py`/`zero/rpc.py`, and `zero.cli` has no command that bypasses `controller` mode/arm checks.

- [ ] **Step 2: Write failing signer tests**

Fake `subprocess.run` and assert address resolution executes:

```text
cast wallet address --keystore <path> --password-file <path>
```

Signing must execute `cast mktx` with `--chain 42161`, `--legacy`, explicit `--nonce`, `--gas-limit`, `--gas-price`, `--keystore`, and `--password-file`, followed by the executor address and the exact `run(...)` signature/arguments from `cast_run_args()`.

Assert no password contents or raw private-key flags are ever placed in argv, logs, or exceptions.

- [ ] **Step 3: Implement `FoundryKeystoreSigner`**

Validate keystore/password files exist and are regular files. Validate derived address equals configured `ZERO_SIGNER_ADDRESS` when provided. Require signed output to be one `0x`-prefixed raw transaction line; otherwise raise `SignerError`.

- [ ] **Step 4: Write failing broadcaster/replacement tests**

```python
assert replacement_gas_price(100, 105) == 113  # >=12.5% bump, ceil
assert Broadcaster(rpc).submit("0xabc") == "0xhash"
```

`submit()` must call only `eth_sendRawTransaction`; transport errors do not fabricate a hash.

- [ ] **Step 5: Implement broadcaster and commit**

Run: `python3 -m unittest tests.test_live_signer tests.test_live_broadcast tests.test_no_live_broadcast -v`
Expected: PASS.

```bash
git add zero/signer.py zero/broadcast.py zero/rpc.py \
  tests/test_live_signer.py tests/test_live_broadcast.py tests/test_no_live_broadcast.py
git commit -m "feat: add isolated ZERO signer and broadcaster"
```
### Task 6: Paper/live controller state machine

**Files:**
- Create: `zero/controller.py`
- Modify: `zero/execution_policy.py`
- Test: `tests/test_live_controller.py`
- Update: `tests/test_execution_policy.py`

**Interfaces:**
- Produces `Controller(mode, supervisor, head_source, control, ledger, policy, preflight, signer, nonce_manager, broadcaster, rpc, clock)`.
- Produces `async run()` and `handle_head(head: HeadEvent) -> ControllerCycleResult`.
- Paper and live share the same candidate selection, production gate, preflight, and `ExecutionPolicy`; they diverge only after authorization.

- [ ] **Step 1: Extend `ExecutionPolicy` for pending-count enforcement**

Add constructor `max_pending_transactions: int = 1` and `authorize(..., pending_count: int = 0)`. Before reserving a candidate, reject `pending_count >= max_pending_transactions` with `pending_limit_reached`. Existing callers remain compatible through defaults.

Run: `python3 -m unittest tests.test_execution_policy -v`
Expected: first run FAIL on the new pending-count test; after minimal change PASS.

- [ ] **Step 2: Write controller tests with fake dependencies**

Required cases:
- `shadow`: uses the existing fork verifier and records simulated evidence; it never calls live preflight, signer, or broadcaster.
- `paper`: exact preflight + live profit gate + policy pass; records `PAPER`; never signs.
- `live` DISARMED: reaches policy but records rejection `disarmed`; never reserves nonce.
- `live` ARMED: signs and submits exactly one best candidate.
- KILLED: no candidate reaches signer.
- stale block/deadline/profit/gas/loan/pending/duplicate failures never fall through.
- two heads arriving during one scan result in the newest head being scanned next, not an accumulated queue.

- [ ] **Step 3: Implement candidate-to-executor conversion**

Accept only payloads whose executable candidate is a two-pool Uniswap arbitrage. Build two `ExecutorStep` values from candidate `base_asset`, `quote_asset`, `loan_amount_raw`, `hop1_expected_out_raw`, `fee1`, `fee2`, and production minimum profit. Reject `multidex_exact`, `multihop_exact`, liquidation, or missing execution fields in this first live slice.

- [ ] **Step 4: Implement the ordered controller pipeline**

On `live` controller startup, call `control.disarm(now=clock())` before consuming heads so a previous ARM never survives restart. Before each new live scan, reconcile existing `SIGNED|SUBMITTED|REPLACED` rows. For each head: run the mode-appropriate supervisor; `shadow` keeps the existing fork verifier, while `paper|live` use `verifier=None`; rank candidates descending; for each candidate do production gate -> deadline/freshness -> executor request -> pending `eth_call` preflight -> `ExecutionPolicy.authorize()` -> mode/ARM check.

In `paper`, transition the ledger row to `PAPER` and stop. In `live`, reserve nonce, obtain network gas price, sign, compute `tx_hash = keccak256(raw_signed_tx_bytes)`, persist `SIGNED` with nonce/hash **before** broadcast, submit, require provider hash equals precomputed hash, then transition to `SUBMITTED`.

- [ ] **Step 5: Define transport ambiguity behavior**

If broadcast raises after signing, keep state `SIGNED`; do not sign a second transaction. Reconciliation checks the precomputed hash first. A hash mismatch, invalid state transition, or signer identity change triggers `control.kill("transaction_integrity")`.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m unittest tests.test_execution_policy tests.test_live_controller -v`
Expected: PASS.

```bash
git add zero/controller.py zero/execution_policy.py \
  tests/test_execution_policy.py tests/test_live_controller.py
git commit -m "feat: add guarded ZERO paper and live controller"
```
### Task 7: Receipt reconciliation, realized P&L, and live reserve evidence

**Files:**
- Create: `zero/reconcile.py`
- Modify: `zero/ledger.py`
- Modify: `zero/execution_policy.py`
- Test: `tests/test_live_reconcile.py`
- Update: `tests/test_execution_policy.py`

**Interfaces:**
- Produces `Reconciler.reconcile_pending() -> ReconcileSummary`.
- Produces `decode_execution_completed(receipt, executor) -> ExecutionCompletedEvent | None`.
- Produces `ledger.live_economics(limit=100)` with the same `success/predicted_net/realized_net/model_error` shape consumed by `AdaptiveReserve`.

The event topic is `keccak256("ExecutionCompleted(address,uint256,uint256,uint256)")`. `asset` is indexed in `topics[1]`; data words are `amount`, `realizedProfit`, `endingBalance`.

- [ ] **Step 1: Write failing receipt/P&L tests**

For a successful receipt, assert:

```python
realized_profit_usd = realized_profit_raw / 10**base_decimals * base_price_usd
gas_cost_usd = gas_used * effective_gas_price / 1e18 * eth_price_usd
realized_net_usd = realized_profit_usd - gas_cost_usd
model_error_usd = realized_net_usd - predicted_net_usd
```

A status-0 receipt transitions to `REVERTED`, records gas as live loss, and increments consecutive reverts. A status-1 receipt missing `ExecutionCompleted` is an integrity failure and triggers KILL.

- [ ] **Step 2: Implement pinned execution-block price lookup**

Use Aave oracle prices at `mined_block` for the base asset and configured WETH gas asset. Do not use current/latest prices for receipt accounting.

- [ ] **Step 3: Add live economics and separate adaptive reserve**

`ledger.live_economics()` returns mined/reverted rows only. Reuse `AdaptiveReserve` over those rows; controller uses:

```python
effective_reserve_usd = max(fork_reserve_usd, live_reserve_usd)
```

so fork and live evidence remain separately auditable while the stricter reserve controls admission.

- [ ] **Step 4: Fix daily-loss accounting semantics**

`ExecutionPolicy.record_outcome()` must add `max(0, loss_usd)` to the daily loss budget for both successful and reverted transactions; successful execution resets only the consecutive-revert counter. Preserve the existing parameter name for compatibility, but document that live callers pass actual realized loss.

- [ ] **Step 5: Implement restart/pending reconciliation**

For states `SIGNED|SUBMITTED|REPLACED`: receipt found -> settle; tx found without receipt -> remain pending; no tx and chain pending nonce greater than reserved nonce -> `DROPPED` + `KILL nonce_conflict`. Before that condition, never reuse the nonce.

If a submitted transaction exceeds `receipt_timeout_s`, and `replacement_count < max_replacement_attempts`, re-sign the identical executor request with the same nonce and `replacement_gas_price()`, persist the new precomputed hash plus previous hash in audit detail, increment `replacement_count`, and set state `REPLACED` before broadcasting it.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m unittest tests.test_live_reconcile tests.test_execution_policy -v`
Expected: PASS.

```bash
git add zero/reconcile.py zero/ledger.py zero/execution_policy.py \
  tests/test_live_reconcile.py tests/test_execution_policy.py
git commit -m "feat: reconcile ZERO live receipts and pnl"
```
### Task 8: Live doctor and operator CLI

**Files:**
- Create: `zero/live_doctor.py`
- Modify: `zero/cli.py`
- Modify: `config/arbitrum.json`
- Modify: `.env.example`
- Test: `tests/test_live_doctor.py`
- Test: `tests/test_live_cli.py`

**Interfaces:**
- Produces `run_live_doctor(cfg, rpc, signer) -> DoctorReport` with named checks and `ok` aggregate.
- CLI commands: `controller`, `live-doctor`, `arm`, `disarm`, `kill`, `reconcile`.

- [ ] **Step 1: Write failing live-doctor tests**

Checks must include: chain ID `42161`; WebSocket URL configured; broadcast RPC configured; executor address valid; executor bytecode non-empty; bytecode keccak equals configured hash; executor `pool()` equals current Aave pool; `paused()` is false; configured signer address equals keystore-derived address; `authorizedCaller(signer)` is true; canonical SwapRouter02 is allowed; `exactInputSingle` selector is allowed; each configured live loan asset/token is allowlisted; on-chain max loan is nonzero and not below the configured live cap.

Any failed check makes report `ok=False`. No doctor check sends a transaction.

- [ ] **Step 2: Implement contract read helpers**

Use sender-free `eth_call` with `selector_hex()` and ABI word decoding. Add a local bytes4-word encoder for `allowedSelector(address,bytes4)` so the selector is left-aligned and right-padded to 32 bytes.

- [ ] **Step 3: Write failing CLI tests**

Assert:
- `controller --mode live` is accepted but starts DISARMED.
- `arm` refuses if live-doctor fails or control state is killed.
- `arm` writes `mode=live, armed=true` only after doctor success.
- `disarm` is always allowed.
- `kill --reason operator` forces `armed=false` and persists the reason.
- `reconcile` invokes one reconciliation pass and never scans for a new candidate.

- [ ] **Step 4: Wire environment overrides**

`load_config()` reads `ZERO_MODE`, `ZERO_LIVE_ARM_REQUIRED`, `ZERO_WS_URL`, `ZERO_BROADCAST_RPC_URL`, `ZERO_EXECUTOR_ADDRESS`, `ZERO_EXECUTOR_CODE_HASH`, `ZERO_SIGNER_ADDRESS`, `ZERO_SIGNER_BACKEND`, `ZERO_KEYSTORE_PATH`, and `ZERO_KEYSTORE_PASSWORD_FILE`. Empty secret references are allowed in shadow mode and rejected by live-doctor for live mode. `ZERO_LIVE_ARM_REQUIRED` must equal `1` in live mode; any other value is a configuration failure, never an auto-arm switch.

- [ ] **Step 5: Wire controller construction**

`cmd_controller` builds `WebSocketHeadSource`, `ControlStore`, `ExecutionPolicy`, preflight, ledger, nonce manager, and reconciler. In `shadow`, construct `PnlSwarmSupervisor` with the existing structured fork verifier. In `paper|live`, construct it with `verifier=None` because the controller owns pending-state preflight. Instantiate signer/broadcaster only for live mode; paper mode uses configured non-secret signer address for `eth_call` sender context and cannot construct a signing backend.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m unittest tests.test_live_doctor tests.test_live_cli -v`
Expected: PASS.

```bash
git add zero/live_doctor.py zero/cli.py config/arbitrum.json .env.example \
  tests/test_live_doctor.py tests/test_live_cli.py
git commit -m "feat: add ZERO live doctor and operator commands"
```
### Task 9: Runtime telemetry and live-vs-simulated TUI separation

**Files:**
- Modify: `zero/runtime.py`
- Modify: `zero/tui_data.py`
- Modify: `zero/tui.py`
- Test: `tests/test_live_runtime.py`
- Update: `tests/test_tui_data.py`
- Update: `tests/test_tui_render.py`

**Interfaces:**
- `RuntimeStatus` adds `mode`, `armed`, `signer_address`, `executor_address`, `executor_validated`, `pending_transactions`, `active_nonce`, `last_tx_hash`, `last_tx_state`, `production_gate_rejects`, `consecutive_reverts`, `daily_live_loss_usd`, and `live_reserve_usd`.
- `MonitoringSnapshot` adds a separate `live_pnl: LivePnlSummary`.

- [ ] **Step 1: Write failing runtime telemetry tests**

`RuntimeMonitor.record_controller(...)` must atomically update controller fields without resetting scanner timing/heartbeat fields. A KILL state remains red/visible even when the process heartbeat is fresh.

- [ ] **Step 2: Add live P&L aggregation**

Query only settled `MINED|REVERTED` live rows. `LivePnlSummary` includes session/day/all-time realized net, best/worst transaction, mined successes, reverts, pending count, and recent tx rows. Never read fork rows for this summary.

- [ ] **Step 3: Make process detection controller-aware**

Replace the swarm-only command check with a ZERO runtime check accepting either `zero.cli swarm` or `zero.cli controller`. Preserve PID-age validation and heartbeat fallback.

- [ ] **Step 4: Write failing render/color tests**

Control floor must contain sections in this order: `ZERO CONTROL FLOOR`, `LIVE EXECUTION`, `LIVE WALLET P&L`, `SWARM`, `FORK / SHADOW P&L - SIMULATED, NOT WALLET P&L`, recent live tx, recent fork results, errors.

Assert `MODE LIVE | ARMED` is green, `DISARMED` yellow, `KILLED` red, negative live realized P&L red, positive live realized P&L green, and every fork/simulated P&L line remains magenta.

- [ ] **Step 5: Implement rendering**

Show signer/executor addresses but never keystore/password paths. Show active nonce, pending count, last tx hash/status, daily loss budget, live reserve, and production-gate rejects. Keep existing timing fields visible.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m unittest tests.test_live_runtime tests.test_tui_data tests.test_tui_render -v`
Expected: PASS.

```bash
git add zero/runtime.py zero/tui_data.py zero/tui.py \
  tests/test_live_runtime.py tests/test_tui_data.py tests/test_tui_render.py
git commit -m "feat: add ZERO live execution telemetry"
```
### Task 10: Rollout gate, runbook, launcher, and end-to-end verification

**Files:**
- Modify: `zero/live_doctor.py`
- Modify: `zero/cli.py`
- Modify: `README.md`
- Create: `docs/runbooks/zero-live.md`
- Create: `scripts/zero_controller_tui.sh`
- Test: `tests/test_live_rollout_gate.py`
- Update: `tests/test_tui_launchers.py`

**Interfaces:**
- ARM rollout evidence gate requires at least `100` reserve-eligible measured fork outcomes and positive aggregate fork realized simulation P&L, in addition to live-doctor checks.
- Launcher starts controller + two read-only TUIs; it never invokes `arm`.

- [ ] **Step 1: Write failing rollout-gate tests**

Create ledgers with 99 eligible fork outcomes, 100 outcomes with aggregate `<=0`, and 100 outcomes with aggregate `>0`. Only the third satisfies the evidence gate. Infrastructure/invalid-harness rows do not count toward 100.

- [ ] **Step 2: Enforce the evidence gate in `arm`**

Add live config `min_fork_samples_for_live: 100` and `require_positive_fork_aggregate: true`. `arm` prints each failed rollout check and exits nonzero without changing control state.

- [ ] **Step 3: Add the non-arming tmux launcher**

`scripts/zero_controller_tui.sh` loads `.env`, defaults `ZERO_MODE=paper`, refuses an unknown mode, and runs:

```text
pane 0: python3 -u -m zero.cli controller --mode $ZERO_MODE
pane 1: ./scripts/zero_tui.sh
pane 2: ./scripts/zero_process_tui.sh
```

If `ZERO_MODE=live`, the controller still starts DISARMED. The launcher contains no `arm` command.

- [ ] **Step 4: Write the runbook with exact operating sequence**

Document:

```bash
cd ~/flash-loan
python3 -m pip install -r requirements.txt
bash scripts/cli_test.sh
forge build
bash scripts/hardened_executor_fork_test.sh
cp .env.example .env
```

Then include this executor deployment/configuration sequence using environment variables rather than literal secrets:

```bash
export AAVE_PROVIDER=0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb
export SWAP_ROUTER=0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45
export USDC=0xaf88d065e77c8cc2239327c5edb3a432268e5831
export WETH=0x82aF49447D8a07e3bd95BD0d56f35241523fBab1
export AAVE_POOL="$(cast call "$AAVE_PROVIDER" "getPool()(address)" --rpc-url "$ZERO_BROADCAST_RPC_URL")"
mkdir -p run
forge create contracts/src/ZeroExecutor.sol:ZeroExecutor \
  --constructor-args "$AAVE_POOL" --rpc-url "$ZERO_BROADCAST_RPC_URL" \
  --keystore "$ZERO_KEYSTORE_PATH" --password-file "$ZERO_KEYSTORE_PASSWORD_FILE" \
  --broadcast --json > run/zero-executor-deploy.json
export ZERO_EXECUTOR_ADDRESS="$(python3 -c 'import json; print(json.load(open("run/zero-executor-deploy.json"))["deployedTo"])')"
export ZERO_SIGNER_ADDRESS="$(cast wallet address --keystore "$ZERO_KEYSTORE_PATH" --password-file "$ZERO_KEYSTORE_PASSWORD_FILE")"
cast send "$ZERO_EXECUTOR_ADDRESS" "setToken(address,bool)" "$USDC" true --rpc-url "$ZERO_BROADCAST_RPC_URL" --keystore "$ZERO_KEYSTORE_PATH" --password-file "$ZERO_KEYSTORE_PASSWORD_FILE"
cast send "$ZERO_EXECUTOR_ADDRESS" "setToken(address,bool)" "$WETH" true --rpc-url "$ZERO_BROADCAST_RPC_URL" --keystore "$ZERO_KEYSTORE_PATH" --password-file "$ZERO_KEYSTORE_PASSWORD_FILE"
cast send "$ZERO_EXECUTOR_ADDRESS" "setRouter(address,bool)" "$SWAP_ROUTER" true --rpc-url "$ZERO_BROADCAST_RPC_URL" --keystore "$ZERO_KEYSTORE_PATH" --password-file "$ZERO_KEYSTORE_PASSWORD_FILE"
export SWAP_SELECTOR="$(cast sig "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))")"
cast send "$ZERO_EXECUTOR_ADDRESS" "setSelector(address,bytes4,bool)" "$SWAP_ROUTER" "$SWAP_SELECTOR" true --rpc-url "$ZERO_BROADCAST_RPC_URL" --keystore "$ZERO_KEYSTORE_PATH" --password-file "$ZERO_KEYSTORE_PASSWORD_FILE"
cast send "$ZERO_EXECUTOR_ADDRESS" "setMaxLoan(address,uint256)" "$USDC" 1000000000 --rpc-url "$ZERO_BROADCAST_RPC_URL" --keystore "$ZERO_KEYSTORE_PATH" --password-file "$ZERO_KEYSTORE_PASSWORD_FILE"
cast send "$ZERO_EXECUTOR_ADDRESS" "setMaxSteps(uint256)" 2 --rpc-url "$ZERO_BROADCAST_RPC_URL" --keystore "$ZERO_KEYSTORE_PATH" --password-file "$ZERO_KEYSTORE_PASSWORD_FILE"
export ZERO_EXECUTOR_CODE_HASH="$(cast keccak "$(cast code "$ZERO_EXECUTOR_ADDRESS" --rpc-url "$ZERO_BROADCAST_RPC_URL")")"
```

After deployment, document `python3 -m zero.cli controller --mode paper` for the soak, then `python3 -m zero.cli live-doctor`, `python3 -m zero.cli controller --mode live` (DISARMED), and separate `python3 -m zero.cli arm`, `disarm`, `kill --reason operator`, and `reconcile` commands. State explicitly that predicted/fork P&L is not wallet P&L and no profit is guaranteed.

- [ ] **Step 5: Update README safety boundary**

Replace the obsolete statement that the Python runtime can never broadcast with the precise new boundary: shadow default; paper never signs; live can broadcast only through the isolated controller after doctor/preflight/policy/ARM; no raw keys in repo; Sushi/Camelot remain non-live.

- [ ] **Step 6: Run complete verification**

```bash
python3 -m pip install -r requirements.txt
bash scripts/cli_test.sh
forge build
bash scripts/hardened_executor_fork_test.sh
python3 -m zero.cli swarm-once --ledger run/final-shadow-ledger.db
git diff --check
```

Expected: all Python/CLI tests PASS, Solidity build PASS, hardened executor fork fixture PASS, shadow scan returns without signing/broadcast, and `git diff --check` is silent.

- [ ] **Step 7: Verify secret and arm invariants**

```bash
grep -R "ZERO_PRIVATE_KEY\|PRIVATE_KEY=" -n zero config .env.example scripts docs/runbooks || true
grep -n "arm" scripts/zero_controller_tui.sh
```

Expected: no raw-key configuration; the launcher grep must not show an executable arm command.

- [ ] **Step 8: Commit docs/launcher/final gate**

```bash
git add zero/live_doctor.py zero/cli.py README.md docs/runbooks/zero-live.md \
  scripts/zero_controller_tui.sh tests/test_live_rollout_gate.py tests/test_tui_launchers.py \
  config/arbitrum.json
git commit -m "docs: add ZERO live rollout runbook"
```
