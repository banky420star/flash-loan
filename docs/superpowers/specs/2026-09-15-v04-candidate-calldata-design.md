# ZERO Engine v0.4 — Candidate to Calldata Design

## Goal

Translate a PASS arbitrage opportunity from the existing Arbitrum shadow scanner into deterministic, fork-executable Uniswap V3 SwapRouter02 calldata, execute it atomically inside the existing Aave V3 flash-loan verifier, and record predicted-vs-realized economics.

## Scope

v0.4 is limited to the existing two-pool Uniswap V3 round trip configured in `config/arbitrum.json`:

- base asset: USDC
- quote asset: WETH
- leg 1: base -> quote through configured pool fee tier
- leg 2: quote -> base through configured pool fee tier
- flash liquidity: Aave V3 `flashLoanSimple`
- execution environment: Anvil/Foundry fork of Arbitrum One only

No production wallet, signer, nonce manager, private key, MEV relay, or mainnet broadcast path is added.

## Canonical contracts

- Arbitrum chain id: `42161`
- Uniswap V3 factory: existing config value
- Uniswap SwapRouter02: `0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45`
- Aave Pool: resolved at runtime from the configured PoolAddressesProvider

The SwapRouter02 V3 `exactInputSingle` ABI is:

`exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))`

SwapRouter02 uses `amountIn == 0` as its `CONTRACT_BALANCE` sentinel. The first leg sends its output to router custody using `recipient = address(2)` (`ADDRESS_THIS`), and the second leg uses `amountIn = 0` and `recipient = address(1)` (`MSG_SENDER`) so the router consumes the exact intermediate balance and returns final base tokens to `ZeroForkExecutor`.

## Candidate model

Create an immutable `ArbitrageCandidate` containing:

- `block`
- `name`
- `base_asset`
- `quote_asset`
- `base_decimals`
- `quote_decimals`
- `loan_size`
- `loan_amount_raw`
- `hop1_expected_out`
- `hop1_expected_out_raw`
- `hop2_expected_out`
- `hop2_expected_out_raw`
- `fee1`
- `fee2`
- `flash_premium_bps`
- `flash_fee`
- `gas_cost_usd`
- `gross_profit`
- `predicted_net`
- `min_profit`

The scanner must include `hop1_out` and `hop2_out` in each profit-curve point so the candidate bridge does not recompute using different state.

## Flash-loan economics

Add `AaveV3.flashloan_premium_total(pool)` using the on-chain `FLASHLOAN_PREMIUM_TOTAL()` view function. The premium is expressed in basis points and must be read from the same live/fork Pool used by the scanner.

For loan size `q`:

`flash_fee = q * premium_bps / 10_000`

`predicted_net = gross_profit - gas_cost_usd - flash_fee`

The existing Gate evaluates this premium-adjusted `predicted_net`.

## Calldata bridge

Create a pure Python encoder that emits three ordered `ExecutionStep` values:

1. `USDC.approve(SwapRouter02, loan_amount_raw)`
2. `SwapRouter02.exactInputSingle(base -> quote)`
   - `recipient = address(2)`
   - `amountIn = loan_amount_raw`
   - `amountOutMinimum = floor(hop1_expected_out_raw * (1 - slippage_bps / 10_000))`
   - `sqrtPriceLimitX96 = 0`
3. `SwapRouter02.exactInputSingle(quote -> base)`
   - `recipient = address(1)`
   - `amountIn = 0` (router CONTRACT_BALANCE sentinel)
   - `amountOutMinimum = loan_amount_raw + flash_fee_raw + min_profit_raw`
   - `sqrtPriceLimitX96 = 0`

Default `slippage_bps` for fork verification is 20 bps and is configurable under `arbitrage.execution`.

The encoder must use the repository's Ethereum Keccak implementation for selectors and static ABI words. No web3 dependency is introduced.

## Fork execution bridge

Add a Foundry test/script path that:

1. forks Arbitrum at the candidate block;
2. resolves the live Aave Pool;
3. deploys `ZeroForkExecutor`;
4. constructs the three v0.4 steps using the candidate parameters;
5. runs `ZeroForkExecutor.run(...)`;
6. confirms Aave repayment and minimum-profit enforcement;
7. reports gas and realized base-token profit.

The real-fork test may use deterministic test-only state funding solely to pay the flash premium when testing bridge mechanics, but an end-to-end arbitrage acceptance test must execute the two real Uniswap pools and must not inject artificial profit into the executor.

## CLI

Add:

- `zero candidate` — run one shadow scan and print normalized PASS arbitrage candidates as JSON.
- `zero calldata` — build calldata steps for the best current PASS candidate and print targets/data.
- `zero fork-arb` — run the candidate-to-calldata fork replay using Foundry.

`fork-arb` remains a local/fork verification command and must never send writes to the configured upstream Arbitrum RPC.

## Ledger

Fork verification rows retain existing fields and add enough detail JSON to identify:

- candidate name
- loan raw amount
- fee tiers
- flash premium bps
- min profit raw
- router address
- calldata step hashes/selectors

## Safety invariants

- upstream Arbitrum RPC is read-only;
- write-capable paths require loopback RPC;
- no private keys;
- no production signer;
- no `eth_sendRawTransaction` to Arbitrum One;
- only configured router and candidate token addresses may be encoded;
- candidate block must equal the fork block used for verification;
- second-leg minimum output must cover principal + actual flash premium + configured minimum profit.

## Acceptance criteria

- existing v0.3 tests remain green;
- flash premium query is unit tested;
- profit curve preserves both hop outputs;
- candidate normalization is deterministic;
- approve calldata selector and words are tested;
- SwapRouter02 exactInputSingle calldata selector/layout is tested against Solidity decoding;
- second leg uses router-balance sentinel `amountIn = 0`;
- remote write guard still rejects non-loopback targets;
- Solidity compiles with Foundry;
- real Arbitrum fork executes the generated two-swap route through Aave flashLoanSimple;
- Aave loan repays atomically;
- fork result records gas and realized-vs-predicted net;
- no production broadcast path exists.
