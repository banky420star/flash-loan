# ⚡ ZERO Engine — v0.4 Candidate Fork Replay

ZERO Engine is an Arbitrum One opportunity scanner with a **local-only fork
execution verifier**. v0.4 connects the scanner to executable Aave + Uniswap
calldata: a PASS arbitrage candidate can now be normalized, encoded, and
replayed at its exact detection block without adding any production signing or
mainnet broadcast path.

```text
ARBITRUM ONE (read-only RPC)
        │
        ▼
 Aave V3 + Uniswap V3 state
        │
        ▼
 size-swept arbitrage scanner
        │
        ├── live Aave flash premium
        ├── gas estimate
        └── risk/profit gate
        │
        ▼
 PASS candidate @ block N
        │
        ▼
 ArbitrageCandidate
        │
        ▼
 deterministic calldata
   1. USDC.approve(SwapRouter02)
   2. USDC → WETH / 0.05%
   3. WETH → USDC / 0.30%
        │
        ▼
 exact-block Arbitrum fork
        │
        ▼
 ZeroForkExecutor.sol
        │
        ├── Aave flashLoanSimple
        ├── real Uniswap V3 swaps
        ├── principal + premium repayment
        └── minimum-profit enforcement
```

## Safety boundary

v0.4 remains a verification system, not a live trader.

- No private keys.
- No production signer.
- No nonce manager.
- No mainnet transaction broadcaster.
- No `eth_sendRawTransaction` path to Arbitrum One.
- Write-capable RPC targets remain loopback-only in `zero/fork.py`.
- The public Arbitrum RPC is used only for reads and Foundry fork state.
- If the scanner has no PASS opportunity, `fork-arb` exits without executing a route.

## Setup

Python uses only the standard library. For fork execution install Foundry:

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

Check the environment:

```bash
python3 -m zero.cli doctor
python3 -m zero.cli fork-status
```

## Scanner commands

Run one live Arbitrum shadow scan:

```bash
python3 -m zero.cli scan
```

Show only current arbitrage opportunities that passed the risk gate:

```bash
python3 -m zero.cli candidate
```

Run continuously without executing transactions:

```bash
python3 -m zero.cli shadow --interval 30
```

Inspect the opportunity audit ledger:

```bash
python3 -m zero.cli ledger --tail 20
```

## v0.4 candidate → calldata

Print the executable representation of the highest predicted-net PASS
candidate:

```bash
python3 -m zero.cli calldata
```

The output contains the normalized candidate plus three ordered steps:

```text
1. USDC.approve(SwapRouter02, loanAmount)
2. SwapRouter02.exactInputSingle(USDC → WETH, fee=500)
3. SwapRouter02.exactInputSingle(WETH → USDC, fee=3000)
```

The bridge uses canonical Arbitrum SwapRouter02:

```text
0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45
```

The first swap sends WETH to router custody using Uniswap's `address(2)`
recipient sentinel. The second swap uses `amountIn = 0`, Uniswap's
`CONTRACT_BALANCE` sentinel, so it consumes the exact WETH produced by the
first swap, then uses recipient `address(1)` to return USDC to the executor.

The second leg's minimum output must cover:

```text
flash principal
+ live Aave FLASHLOAN_PREMIUM_TOTAL
+ candidate minimum profit
```

## Exact candidate fork replay

The primary v0.4 command is:

```bash
python3 -m zero.cli fork-arb
```

It performs this sequence:

```text
fresh live scan
→ select highest predicted-net PASS candidate
→ encode the three SwapRouter02 calls
→ use the candidate's detection block as FORK_BLOCK
→ pass the exact target/calldata bytes into Foundry
→ replay them through ZeroForkExecutor on that fork
→ require Aave repayment and minimum profit
```

If there is no PASS candidate, the command returns `no_pass_candidate` and does
not create an execution attempt.

For inspection-only use:

```bash
python3 -m zero.cli fork-command --block 505482254
python3 -m zero.cli fork-test --block 505482254
python3 -m zero.cli fork-ledger --tail 20
```

## Deterministic integration smoke

`candidate_fork_test.sh` creates a price discrepancy **only inside the fork** by
trading against a real Uniswap V3 pool from the test account. It then proves
that `ZeroForkExecutor` can:

1. borrow USDC using real Aave V3 `flashLoanSimple`;
2. buy WETH through the real 0.05% USDC/WETH pool;
3. sell the exact received WETH through the real 0.30% pool;
4. repay Aave principal + the live premium; and
5. retain at least the configured minimum profit.

No USDC trading profit is injected into the executor for this test.

Run it directly:

```bash
bash scripts/candidate_fork_test.sh
```

## Tests

Offline Python + CLI suite:

```bash
bash scripts/cli_test.sh
```

Compile the Solidity verification contracts:

```bash
forge build
```

Baseline Aave fork smoke:

```bash
bash scripts/fork_test.sh
```

Aave + Uniswap arbitrage route smoke:

```bash
bash scripts/candidate_fork_test.sh
```

GitHub Actions executes the Python suite, Solidity compilation, baseline Aave
fork test, and candidate Aave+Uniswap fork test on pull requests.

## What changed from v0.3

- Aave flash-loan premium is read on-chain via `FLASHLOAN_PREMIUM_TOTAL()`.
- Flash premium is now included in predicted arbitrage net profit.
- Profit-curve rows retain the exact first- and second-hop outputs.
- PASS opportunities normalize into immutable `ArbitrageCandidate` objects.
- Token quantities are converted to deterministic integer raw units.
- SwapRouter02 calldata is encoded without adding a web3 dependency.
- Only the canonical Arbitrum SwapRouter02 is accepted by the encoder.
- `candidate`, `calldata`, and `fork-arb` CLI commands are available.
- Exact Python-generated target/calldata bytes can be replayed by Foundry at the
  candidate's original detection block.
- A deterministic real Aave + real Uniswap two-pool fork test is part of CI.

## Current limitations

- The scanner currently models a configured two-pool Uniswap V3 USDC/WETH cycle;
  it is not yet a multi-DEX route searcher.
- The scanner's fast quote model is single-range; the exact fork replay is the
  authority when a candidate exists.
- `fork-arb` currently returns the Foundry replay success/failure code. The
  deterministic `ForkResult`/SQLite schema exists, but automatic parsing of
  Foundry's realized token P&L and gas trace back into `fork_verifications` is
  not yet wired for the live-candidate command.
- Liquidation discovery is still watchlist-based rather than a full borrower
  event index.
- No production execution, private-key handling, MEV bidding, or mainnet writes
  are present.
