# ZERO v0.5.3 Adaptive Reserve + Faster Swarm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Learn a conservative reserve from realized fork errors and reduce pinned-block swarm scan latency by batching RPC reads.

**Architecture:** Add a pure reserve-policy module fed by ledger history, compute one reserve at the supervisor boundary per block, and keep worker economics unchanged except for the reserve value they receive. Replace sequential Aave metadata, Uniswap factory discovery, and unique pool-state reads with deterministic pinned-block JSON-RPC batches while preserving exact block consistency.

**Tech Stack:** Python 3.11, sqlite3, JSON-RPC, unittest, Foundry/Forge, Aave V3, Uniswap V3.

**Spec:** `docs/superpowers/specs/2026-09-16-v053-adaptive-reserve-speed-design.md`

## Global Constraints

- Shadow/fork only; no mainnet signer or broadcast path.
- All market reads in one swarm cycle use exactly one pinned Arbitrum block.
- Positive admission remains strict `expected_net > 0` after premium, gas, and reserve.
- SQLite writes remain serialized on the supervisor thread.
- Existing fork verification behavior remains unchanged.

---

### Task 1: Adaptive reserve policy

**Files:**
- Create: `zero/reserve.py`
- Test: `tests/test_adaptive_reserve.py`

**Interfaces:**
- Consumes: rows shaped like `{"success": bool, "predicted_net": float, "realized_net": float, "model_error": float}`.
- Produces: `ReserveEstimate(value_usd: float, samples: int)` and `AdaptiveReserve.estimate(rows) -> ReserveEstimate`.

- [ ] **Step 1: Write failing tests**

Cover bootstrap behavior, adverse-sign handling, favorable errors, failed positive candidates, nearest-rank quantile, floor, and cap.

- [ ] **Step 2: Run targeted test and confirm RED**

Run: `python3 -m unittest tests.test_adaptive_reserve -v`
Expected: import failure because `zero.reserve` does not exist.

- [ ] **Step 3: Implement minimal deterministic reserve module**

Use `Decimal` for financial inputs. Define adverse sample as `max(0, -model_error)` for measured results. For failed positive candidates with no realized profit, use `predicted_net` as the adverse sample. Use nearest-rank quantile index `ceil(q*n)-1`, clamp to floor/cap, and bootstrap until `min_samples`.

- [ ] **Step 4: Run targeted tests and confirm GREEN**

Run: `python3 -m unittest tests.test_adaptive_reserve -v`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat: learn adaptive reserve from fork errors`

### Task 2: Ledger reserve history

**Files:**
- Modify: `zero/ledger.py`
- Test: `tests/test_adaptive_reserve_ledger.py`

**Interfaces:**
- Produces: `Ledger.fork_economics(limit: int = 100) -> list[dict]` ordered newest-first with `success`, `predicted_net`, `realized_net`, and `model_error`.

- [ ] **Step 1: Write failing test**

Insert multiple fork rows and assert exact returned economics and limit behavior.

- [ ] **Step 2: Run targeted test and confirm RED**

Run: `python3 -m unittest tests.test_adaptive_reserve_ledger -v`
Expected: `AttributeError` for missing `fork_economics`.

- [ ] **Step 3: Implement read-only helper**

Query only the four required columns. Do not mutate schema.

- [ ] **Step 4: Run targeted test and confirm GREEN**

Run the targeted unittest module.

- [ ] **Step 5: Commit**

Commit message: `feat: expose fork economics history`

### Task 3: One reserve per pinned block

**Files:**
- Modify: `zero/pnl.py`
- Modify: `config/arbitrum.json`
- Test: `tests/test_adaptive_reserve_supervisor.py`

**Interfaces:**
- `PnlSwarmSupervisor._reserve_estimate() -> ReserveEstimate` reads config + ledger on the supervisor thread.
- `PnlSwarmSupervisor.run_block(block: int | None = None) -> dict` computes one reserve, temporarily freezes it in `swarm.model_reserve_usd`, delegates to `super().run_block(block)`, restores the prior config value, and adds reserve audit fields to the result.

- [ ] **Step 1: Write failing supervisor tests**

Assert two workers in the same cycle observe exactly the same reserve, static fallback is used when disabled, and cycle output includes `adaptive_reserve_usd` and `reserve_samples`.

- [ ] **Step 2: Run targeted tests and confirm RED**

Expected failure because supervisor has no reserve estimator/output fields.

- [ ] **Step 3: Implement minimal wiring**

Compute reserve before worker futures launch. Pass it into the existing `scan_cycle_config(... model_reserve_usd=...)` through the frozen supervisor configuration. Do not query SQLite from worker threads.

- [ ] **Step 4: Add config**

Enable adaptive reserve with `lookback=100`, `min_samples=5`, `quantile=0.90`, `floor_usd=0.0`, `cap_usd=25.0`, `bootstrap_reserve_usd=0.05`.

- [ ] **Step 5: Run tests and confirm GREEN**

Run targeted tests and `bash scripts/cli_test.sh`.

- [ ] **Step 6: Commit**

Commit message: `feat: apply one learned reserve per swarm block`

### Task 4: Generic pinned-block RPC batch helpers

**Files:**
- Modify: `zero/rpc.py`
- Test: `tests/test_rpc_batch_block_tags.py`

**Interfaces:**
- `Rpc.batch_eth_call(calls: list[tuple[str, str]], *, block, max_batch=100) -> list[bytes]` is strict and raises on any member error.
- `Rpc.batch_eth_call_results(calls: list[tuple[str, str]], *, block, max_batch=100) -> list[bytes | RpcError]` preserves member errors for callers that can isolate a bad row.
- Whole-batch transport failures use configured retries/backoff while keeping the same pinned block tag.

- [ ] **Step 1: Write failing tests**

Assert integer block tags are encoded on every call, order is preserved, calls chunk at `max_batch`, transport failures retry, strict member errors raise, and no fallback block is used.

- [ ] **Step 2: Run targeted test and confirm RED**

- [ ] **Step 3: Implement batch helpers**

Build JSON-RPC `eth_call` entries with the same `_block_tag(block)`, chunk deterministically, preserve optional member errors, and retry only whole-request transport failures.

- [ ] **Step 4: Run targeted tests and confirm GREEN**

- [ ] **Step 5: Commit**

Commit message: `feat: add pinned-block batched eth calls`

### Task 5: Batch Aave token registry

**Files:**
- Create/Modify: `zero/swarm_batch.py`
- Test: `tests/test_swarm_batched_registry.py`

**Interfaces:**
- `build_token_registry_batched(engine, block: int, *, pool: str | None = None, oracle: str | None = None, max_batch: int = 100) -> dict[str, TokenInfo]` batches `symbol()`, `decimals()`, and Aave oracle price reads at one pinned block.
- A JSON-RPC member error in one reserve's metadata discards only that reserve; discovery/state batches remain strict.

- [ ] **Step 1: Write failing test**

Use a fake Aave/RPC transport and assert reserve list is fetched once, metadata/price reads are batched at one block, malformed or member-error token rows are skipped, and no latest-block read occurs.

- [ ] **Step 2: Confirm RED**

- [ ] **Step 3: Implement batched registry parser**

Encode `symbol()`, `decimals()`, and `getAssetPrice(address)` using existing encoding helpers/selectors and use tolerant member results only for this registry layer.

- [ ] **Step 4: Confirm GREEN + regression suite**

- [ ] **Step 5: Commit**

Commit message: `perf: batch pinned Aave token metadata`

### Task 6: Batch Uniswap pool discovery

**Files:**
- Modify: `zero/swarm_batch.py`
- Test: `tests/test_swarm_batched_discovery.py`

**Interfaces:**
- `discover_uniswap_routes_batched(engine, pair: tuple[str, str], registry: dict[str, TokenInfo], block: int, fee_tiers: list[int], *, max_batch: int = 100) -> list[dict]` sends configured fee-tier factory lookups in one or more strict pinned batches.

- [ ] **Step 1: Write failing test**

Assert four fee-tier lookups use one batch when under limit, route identities remain unchanged, zero-address pools are skipped, and block tag is preserved.

- [ ] **Step 2: Confirm RED**

- [ ] **Step 3: Implement batch discovery**

Keep existing route construction unchanged after pool-address resolution.

- [ ] **Step 4: Confirm GREEN**

- [ ] **Step 5: Commit**

Commit message: `perf: batch pinned Uniswap pool discovery`

### Task 7: Batch unique pool state

**Files:**
- Modify: `zero/swarm_batch.py`
- Test: `tests/test_swarm_batched_context.py`

**Interfaces:**
- `build_scan_context_batched(engine, block: int, route_catalog: list[dict], *, tokens: dict[str, TokenInfo] | None = None, aave_pool: str | None = None, oracle: str | None = None, max_batch: int = 100) -> ScanContext` batches unique `slot0()` and `liquidity()` reads and rejects mixed-block catalogs before any RPC request.

- [ ] **Step 1: Write failing test**

Assert each unique pool contributes exactly two batch calls, duplicate pool references do not duplicate reads, block is pinned, and a batch failure aborts context construction.

- [ ] **Step 2: Confirm RED**

- [ ] **Step 3: Implement batch pool-state parser**

Decode slot0 first word as `sqrtPriceX96` and liquidity first word as `liquidity`.

- [ ] **Step 4: Confirm GREEN**

- [ ] **Step 5: Commit**

Commit message: `perf: batch pinned Uniswap pool state`

### Task 8: Production wiring and timing instrumentation

**Files:**
- Modify: `zero/pnl.py`
- Modify: `config/arbitrum.json`
- Test: `tests/test_swarm_timings.py`

**Interfaces:**
- `PnlSwarmSupervisor._build_catalog(block, workers)` uses the three batched helpers and configured `swarm.max_rpc_batch`.
- Cycle output adds `catalog_ms`, `scan_ms`, `verify_ms` without removing existing fields.

- [ ] **Step 1: Write failing tests**

Assert the production supervisor calls the batched helpers, timing fields exist and are non-negative, and existing worker/route/fork counters remain unchanged.

- [ ] **Step 2: Confirm RED**

- [ ] **Step 3: Implement monotonic timing boundaries and production batch wiring**

Measure catalog/context, worker scan, and fork verification separately using `time.perf_counter()` while keeping the scheduler and leases unchanged.

- [ ] **Step 4: Confirm GREEN**

Run targeted tests and full `bash scripts/cli_test.sh`.

- [ ] **Step 5: Commit**

Commit message: `feat: use batched market data and expose swarm timings`

### Task 9: Integration verification

**Files:**
- Add: `scripts/swarm_live_smoke.py`
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Run full Python/CLI suite**

Run: `bash scripts/cli_test.sh`
Expected: all tests PASS and CLI smoke PASS.

- [ ] **Step 2: Run Solidity build**

Run: `forge build`
Expected: compile success; existing simulation-executor lint warnings may remain.

- [ ] **Step 3: Run live batched swarm market-data smoke**

Run a PR-only live Arbitrum read smoke with fork execution disabled. Expected: 20 workers, zero worker failures, real route scanning, and non-negative timing fields.

- [ ] **Step 4: Run Aave fork smoke**

Run: `bash scripts/fork_test.sh`
Expected: flash loan callback and repayment PASS.

- [ ] **Step 5: Run deterministic candidate fork smoke**

Run: `bash scripts/candidate_fork_test.sh`
Expected: Aave + Uniswap candidate replay PASS.

- [ ] **Step 6: Open PR and require exact-head CI success**

Do not merge until unit/CLI, Foundry build, live swarm market-data smoke, Aave fork smoke, and candidate fork smoke are all green on the same head SHA.
