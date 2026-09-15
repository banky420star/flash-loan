# ZERO Engine v0.5 — Swarm Market Division Design

## Status
Approved in chat as the next v0.5 slice. Written spec pending final user review before implementation planning.

## Goal
Expand ZERO from a single narrow two-pool scan into one continuously running supervisor with 20 concurrent market-scanning workers. The system must accept arbitrage candidates only when expected net profit remains positive after modeled costs, even when the profit is very small.

This slice remains shadow/fork-verification only. It does not add a production mainnet signer or broadcaster.

## Core principle
Optimize for total verified positive net profit over time, not raw trade count.

A candidate is eligible only when:

`expected_net_profit > 0`

where expected net profit accounts for:
- Aave flash-loan premium,
- swap fees,
- modeled price impact,
- gas,
- slippage/model-error reserve.

The current v0.4 fixed `$2` floor, `4x gas` multiplier, and percentage ROI minimum must not block tiny positive-net shadow/fork candidates in this v0.5 swarm mode.

## Hierarchy

### ZERO CEO
One supervisor owns the scan cycle and global opportunity book. It:
- pins one Arbitrum block for each cycle,
- assigns route leases to managers/workers,
- aggregates candidate results,
- deduplicates candidates,
- ranks candidates by expected net profit,
- dispatches only positive-net candidates to fork verification,
- records candidate economics and fork pass/fail metadata.

The CEO is not counted as one of the 20 scanners.

### Four market managers
Each manager coordinates five scanners. Managers are coordinators, not additional scanner slots.

#### ALPHA — Core stable / ETH
- A1: USDC/WETH
- A2: USDt0/WETH
- A3: DAI/WETH
- A4: USDC.e/WETH
- A5: stable/ETH overflow and newly discovered fee tiers

#### BRAVO — BTC / ETH / stable
- B1: WBTC/WETH
- B2: WBTC/USDC
- B3: tBTC/WETH
- B4: tBTC/USDC
- B5: BTC-route overflow/discovery

#### CHARLIE — Liquid staking/restaking
- C1: wstETH/WETH
- C2: rETH/WETH
- C3: weETH/WETH
- C4: ezETH/WETH
- C5: rsETH/WETH plus overflow

#### DELTA — Dynamic/opportunistic
- D1: ARB/WETH
- D2: LINK/WETH
- D3: AAVE/WETH
- D4: GHO/USDC
- D5: dynamically ranked newly discovered liquid routes

These are primary assignments, not permanent exclusive ownership. Idle capacity may be reallocated by lease.

## Worker model
Each of the 20 workers has four logical responsibilities:

### Scout
Discovers configured/known pools and valid fee-tier combinations for the worker's assigned pair.

### Snapshot
Reads all required state at the CEO-pinned block.

### Optimizer
Tests a ladder of flash-loan sizes and keeps the size with the highest expected net profit rather than the largest loan.

### Accountant
Calculates the candidate economics and rejects non-positive expected net results before reporting upward.

These are logical subworkers inside a scanner process/thread; they are not 80 independent OS processes.

## Route registry and deduplication
Every route has a canonical route ID derived from:
- chain,
- base token,
- quote token,
- pool A,
- pool B,
- fee tier A,
- fee tier B,
- direction.

Only one worker may hold a route lease at a time for a given pinned block.

For Uniswap V3, workers may test all existing useful fee-tier combinations in both directions, including 0.01%, 0.05%, 0.30%, and 1.00% where pools exist.

The initial executable path remains a two-pool Uniswap V3 round trip because v0.4 calldata generation currently requires exactly two pools. Multi-DEX and multi-hop execution remain outside this slice.

## Dynamic work stealing
A worker whose primary assignment has no useful or available route asks its manager for another lease.

Managers may temporarily split an overloaded market section across idle workers. Leases expire at the end of the scan block so ownership can be rebalanced on the next block.

This preserves 20 active scanners without allowing duplicate route scans within a block.

## Exact-block consistency
The v0.4 engine records a block number but its state reads may still use `latest`. v0.5 must change RPC read paths so workers can issue `eth_call` and related state reads with an explicit block tag.

Per cycle:
1. CEO reads block `N`.
2. CEO publishes `scan_block=N`.
3. Every worker reads pool/Aave/oracle state at block `N`.
4. Every candidate carries block `N`.
5. Fork verification replays block `N`.

A candidate built from mixed-block state is invalid.

## Shared opportunity book
The supervisor keeps one in-memory opportunity book for each scan block. Every candidate record includes at least:
- candidate ID,
- route ID,
- manager ID,
- worker ID,
- block,
- route,
- loan size,
- gross profit,
- Aave premium,
- swap/impact cost,
- gas cost,
- model/slippage reserve,
- expected net profit,
- ROI,
- timestamp.

The book is deduplicated by candidate/route identity and ranked primarily by expected net profit.

Tiny positive candidates remain eligible for shadow/fork verification.

## Profit economics
The decision rule for swarm-mode candidate admission is:

`expected_net = gross - flash_fee - gas - model_reserve`

Swap fees and price impact are already represented in the quoted round-trip output and therefore must not be double-counted.

The model reserve is configurable. In shadow/fork mode it may be small so the system gathers evidence from tiny edges. Future live-mainnet execution must use a reserve informed by measured predicted-versus-realized error.

`expected_net <= 0` is always rejected.

## Loan-size optimization
Workers test a configured size ladder between minimum and maximum loan size. The winner is the size with the highest expected net profit, not necessarily the highest percentage return or largest notional.

Large loans that lose profitability due to price impact are rejected even when smaller sizes remain profitable.

## Concurrency
Implementation target:
- one supervisor event loop,
- four manager objects,
- 20 worker tasks,
- shared read-only per-block cache where safe,
- shared ledger writer through a serialized interface,
- bounded concurrency so RPC saturation cannot deadlock the scan loop.

The system must tolerate one worker failure without killing the entire cycle. Worker exceptions are recorded and that route lease is released.

## Safety and invariants
- No private key or production signer is added in this slice.
- No mainnet write/broadcast path is added.
- All execution verification remains exact-block fork execution.
- A worker cannot claim a route already leased for the same block.
- All state used by one candidate must come from the same pinned block.
- A candidate with expected net profit <= 0 is never sent to the fork verifier.
- Duplicate candidates are not fork-verified twice for the same block.
- RPC concurrency is capped/configurable.
- Ledger writes are serialized.

## Configuration
Add a swarm section with at least:
- enabled,
- worker_count = 20,
- manager_count = 4,
- max_rpc_concurrency,
- model_reserve_usd or reserve policy,
- size ladder / min-max sizing,
- route assignments,
- work stealing enabled,
- scan cadence/block-driven mode.

The manager/worker IDs and primary pair assignments above become default configuration, not hard-coded business logic.

## Observability
Every scan cycle should report:
- pinned block,
- active workers,
- routes scanned,
- worker failures,
- candidates detected,
- positive-net candidates,
- duplicate candidates suppressed,
- best expected net,
- fork verifications attempted/passed/failed where enabled,
- elapsed cycle time.

## Testing requirements
Before merge:
1. Unit tests for canonical route IDs and route leases.
2. Tests proving two workers cannot own the same route for one block.
3. Tests proving work stealing reallocates idle capacity.
4. Tests proving explicit block tags propagate through RPC/state reads.
5. Tests proving mixed-block candidate data is rejected.
6. Tests proving `expected_net > 0` accepts tiny positive values and rejects zero/negative values.
7. Tests proving 20 workers are created across four managers from configuration.
8. Concurrency tests showing worker failure does not abort the supervisor cycle.
9. Existing 59+ Python/CLI tests remain green.
10. Foundry build remains green.
11. Existing Aave fork smoke remains green.
12. Existing deterministic Aave+Uniswap candidate fork smoke remains green.

## Out of scope for this slice
- production mainnet signing/broadcast,
- multi-DEX execution,
- arbitrary multi-hop calldata,
- real-time mempool/private-orderflow integration,
- liquidation worker expansion,
- learned adaptive reserve model,
- automatic realized P&L ingestion from Foundry into the ledger,
- VPS/service packaging,
- wallet/nonce management,
- cross-chain scanning.

Those remain subsequent v0.5/v0.6 work.
