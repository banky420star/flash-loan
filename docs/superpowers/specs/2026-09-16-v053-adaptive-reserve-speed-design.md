# ZERO v0.5.3 Adaptive Reserve + Faster Swarm Design

## Goal

Turn ZERO's accumulated exact-fork prediction errors into a conservative, data-driven model reserve and reduce the live 20-worker scan cycle without changing the pinned-block safety model.

## Scope

This release changes only the existing Uniswap V3 swarm pipeline. It does not add new DEXes, multi-hop routing, live signing, transaction broadcasting, liquidation execution, or production-key handling.

## Economic invariant

A candidate is executable-positive only when:

`exact_or_prefilter_gross_usd - aave_premium_usd - modeled_gas_usd - adaptive_reserve_usd > 0`

The reserve may only reduce expected net. It can never make a non-positive candidate positive.

## Adaptive reserve

Create `zero/reserve.py` with a small, deterministic `AdaptiveReserve` component. It consumes recent `fork_verifications.model_error` values from the ledger.

`model_error = realized_net - predicted_net`.

Only adverse misses contribute to reserve:

`adverse_error = max(0, -model_error)`.

Policy:
- configurable lookback count, default 100 structured fork results;
- configurable minimum sample count, default 5;
- configurable adverse quantile, default 0.90;
- configurable floor, default 0.0 USD;
- configurable cap, default 25.0 USD;
- before `min_samples`, use `max(floor, configured_bootstrap_reserve_usd)`;
- once enough samples exist, reserve is the nearest-rank adverse quantile, clamped to `[floor, cap]`;
- failures with no measured realized P&L may be included as adverse misses equal to the lost predicted positive edge only when `predicted_net > 0`; this keeps repeated exact-fork false positives from remaining free.

The ledger exposes a read-only helper returning recent fork verification economics. The supervisor computes one reserve per pinned block before workers evaluate routes and passes the same value to every worker for that block. The chosen reserve is included in cycle output and candidate payloads for auditability.

## Fast pinned-block data acquisition

The current scan spends most of its time in sequential RPC calls for:
- Aave reserve symbol/decimals/price metadata;
- Uniswap factory `getPool` discovery across fee tiers;
- `slot0()` and `liquidity()` for unique pools.

Add batch-capable helpers while preserving the exact same block tag on every call.

### Token registry

Resolve the Aave reserve list once. Build one JSON-RPC batch containing `symbol()`, `decimals()`, and oracle `getAssetPrice(address)` calls for every reserve. Parse results in reserve-list order. A failed row is skipped; a malformed batch never causes mixed-block fallback reads.

### Pool discovery

For each unique worker pair, create the four configured `getPool(address,address,uint24)` calls and issue them as one pinned-block batch. Route construction remains unchanged.

### Pool state

For every unique discovered pool, issue `slot0()` and `liquidity()` as one batch. Build the frozen `ScanContext` only after the entire batch succeeds.

## Concurrency and safety

- worker count remains 20;
- RPC batch size is bounded by `swarm.max_rpc_batch`, default 100;
- batches are chunked deterministically when larger than the bound;
- all calls use the same pinned block N;
- no fallback to `latest` is allowed inside a pinned cycle;
- SQLite writes remain on the supervisor thread;
- fork verification remains bounded separately by `max_fork_concurrency`.

## Output and observability

`swarm-once` and continuous `swarm` results add:
- `adaptive_reserve_usd`;
- `reserve_samples`;
- `catalog_ms`;
- `scan_ms`;
- `verify_ms`.

This lets us distinguish market-data latency from worker CPU time and fork replay time.

## Configuration

Under `swarm`:

```json
"adaptive_reserve": {
  "enabled": true,
  "lookback": 100,
  "min_samples": 5,
  "quantile": 0.90,
  "floor_usd": 0.0,
  "cap_usd": 25.0,
  "bootstrap_reserve_usd": 0.05
},
"max_rpc_batch": 100
```

The old static `model_reserve_usd` remains as a compatibility fallback only when adaptive reserve is disabled.

## Tests

Required RED/GREEN coverage:
1. reserve uses adverse error sign correctly;
2. reserve ignores favorable errors;
3. reserve uses bootstrap before enough samples;
4. reserve quantile and cap are deterministic;
5. repeated failed positive fork candidates increase reserve evidence;
6. all workers in one cycle receive the same reserve;
7. batched token metadata calls carry the pinned block;
8. batched pool discovery carries the pinned block;
9. batched pool-state reads carry the pinned block;
10. batch chunking respects `max_rpc_batch`;
11. malformed/failed batch does not silently fall back to latest;
12. existing unit/CLI suite stays green;
13. Foundry build stays green;
14. real Aave fork smoke and deterministic Aave+Uniswap candidate fork smoke stay green.

## Success criteria

- ZERO rejects a candidate whenever the learned reserve removes its positive executable edge.
- Repeated prediction misses automatically make subsequent admission more conservative.
- Pinned-block consistency remains exact.
- The number of HTTP round trips for token metadata, factory discovery, and pool state is reduced from one-per-read to bounded batches.
- No mainnet transaction signing or broadcasting is introduced.
