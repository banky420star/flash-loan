# ZERO Roadmap Completion Design

## Goal
Complete the shadow/fork ZERO Engine roadmap without enabling remote signing or mainnet broadcasting.

## Safety boundary
- All execution remains exact-block fork/shadow only.
- `ZeroForkExecutor.sol` remains simulation-only and is never promoted to production.
- A separate hardened `ZeroExecutor.sol` may be compiled/tested, but no key management or broadcast path is enabled.
- Candidate admission remains `expected executable net > 0` after premium, modeled gas, reserve, and exact venue quote.

## Release sequence
1. v0.5.4 Evidence hygiene: classify fork outcomes so infrastructure/tooling failures do not train the adaptive reserve.
2. v0.5.5 Multi-DEX: introduce venue adapters and add Uniswap V3, Sushi V3, and Camelot Algebra V3 discovery/quote metadata.
3. v0.5.6 Multi-hop: build bounded cyclic route graphs with at most three swaps, starting and ending in the flash asset.
4. v0.5.7 Liquidation swarm: replace fixed close-factor/bonus assumptions with state-aware liquidation candidates and route unwinds.
5. v0.5.8 Always-on runtime: health, RPC failover, heartbeat, WAL ledger, restart-safe service configuration.
6. v0.6.0 Hardened executor: typed/allowlisted execution contract plus off-chain execution policy, still fork-only in this repository.

## Fork evidence model
Fork results gain an explicit `outcome_class`:
- `measured_success`: replay completed and machine-readable realized P&L exists.
- `execution_revert`: fork launched and the strategy transaction reverted for an execution/economic reason.
- `infrastructure_error`: missing Forge, RPC/network failure, timeout, process launch failure.
- `invalid_harness`: malformed/missing result output or internal verification harness failure.
Only measured successes and execution reverts are eligible reserve-learning samples.

## Venue adapter model
Each adapter exposes venue identity, factory/router/quoter allowlists, pool discovery, pinned exact quote, and swap-step encoding metadata. Uniswap V3 and Sushi V3 use the V3 factory `getPool` shape; Camelot Algebra V3 uses its Algebra factory/pool interface. Cross-venue route IDs include venue IDs and pool addresses so identical token paths on different venues cannot collide.

Current Arbitrum deployment references used by configuration:
- Uniswap V3 factory `0x1F98431c8aD98523631AE4a59f267346ea31F984`, QuoterV2 `0x61fFE014bA17989E743c5F6cB21bF9697530B21e`, SwapRouter02 `0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45`.
- Sushi V3 factory `0x1af415a1EbA07a4986a52B6f2e7dE7003D82231e`.
- Camelot Algebra V3 factory `0x1a3c9B1d2F0529D97f2afC5136Cc23e58f1FD35B`, Quoter `0x0Fc73040b26E9bC8514fA028D998E73A254Fa76E`, SwapRouter `0x1F721E2E82F6676FCE4eA07A5958cF098D339e18`.

## Multi-hop model
`RouteCandidate` contains a pinned block, flash/base asset, loan size, ordered legs, predicted gross, flash premium, gas, reserve, and expected executable net. Initial graph search is deterministic, loop-free between endpoints, max three swaps, and cycles must return to the flash asset. Route search never performs remote writes.

## Liquidation model
A liquidation candidate records borrower, health factor, debt asset, collateral asset, maximum repay amount derived from current Aave rules/state, liquidation economics, unwind route, gas, and reserve. The current fixed `0.5` close factor and `0.05` bonus are removed from admission logic. Health factor below 1 is necessary but not sufficient: the candidate must remain positive after flash premium, protocol liquidation economics, exact unwind quote, gas, and reserve.

## Always-on runtime
Add an RPC pool with ordered failover and health scoring, SQLite WAL mode, cycle heartbeat/status snapshots, bounded retry/backoff, and service files for Docker/systemd. A killed/restarted service resumes scanning from current chain state rather than replaying stale candidates. No private keys are accepted by the always-on service in this release.

## Hardened executor
`ZeroExecutor.sol` is a separate contract with owner/pause controls, Aave callback authentication, token/router/selector allowlists, bounded approvals, max steps, max loan amount, deadline/stale-block guard, and minimum profit enforcement. Arbitrary `target.call` is prohibited. Off-chain policy adds nonce/duplicate suppression and daily/revert limits as pure policy objects; signing/broadcast stays disabled.

## Verification gates
Each release requires Python unit/CLI tests and Foundry build. Execution-affecting releases additionally require Aave fork smoke and deterministic candidate fork smoke. Multi-DEX/multi-hop/liquidation features require deterministic fork fixtures or exact pinned `eth_call` smoke tests for supported venues. The final branch must pass all gates on one exact head.