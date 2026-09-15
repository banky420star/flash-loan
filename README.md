# ⚡ ZERO Engine — v0.3 Fork Verification

ZERO Engine is a read-only Arbitrum One opportunity scanner with a **local-only
fork verification stage**. Live chain data is used for detection; any write-capable
simulation is restricted to an Anvil loopback RPC. There is still no private key,
no production signer, and no mainnet broadcast path.

```text
ARBITRUM ONE (read-only RPC)
        │
        ▼
 Aave V3 + Uniswap V3
        │
        ▼
 SCANNERS + RISK GATE
        │
        ▼
 PASS candidate at block N
        │
        ▼
 ANVIL exact-block fork (127.0.0.1 only)
        │
        ▼
 ZeroForkExecutor.sol
        │
        ├── real Aave V3 flashLoanSimple on the fork
        ├── local protocol calls
        ├── repayment enforcement
        └── minimum-profit enforcement
        │
        ▼
 fork verification evidence / SQLite ledger
```

## Shadow-mode commands

```bash
python3 -m zero.cli doctor
python3 -m zero.cli prices
python3 -m zero.cli hf 0x...
python3 -m zero.cli scan
python3 -m zero.cli shadow --interval 30
python3 -m zero.cli ledger
```

## v0.3 fork commands

Install Foundry first (`forge` + `anvil`):

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

Check whether Anvil is available and pin a specific Arbitrum block:

```bash
python3 -m zero.cli fork-status --block 123456789
```

Print the exact local fork command without starting anything:

```bash
python3 -m zero.cli fork-command --block 123456789
```

Run the real Aave V3 flash-loan smoke test against an Arbitrum fork:

```bash
python3 -m zero.cli fork-test --block 123456789
```

Or directly:

```bash
FORK_BLOCK=123456789 bash scripts/fork_test.sh
```

See stored fork-verification evidence:

```bash
python3 -m zero.cli fork-ledger --tail 20
```

## Safety boundary

`zero/fork.py` rejects write targets unless the RPC hostname is one of:

```text
127.0.0.1
localhost
::1
```

The upstream Arbitrum RPC is therefore used only to **read/fork state**. The
simulation executor is intended for local fork use and is explicitly not a
production executor.

## What v0.3 verifies

- Exact-block Anvil command generation.
- Hard rejection of remote write targets.
- A simulation-only Solidity executor with Aave callback validation.
- Real `flashLoanSimple` against the forked Aave V3 Arbitrum deployment.
- Repayment approval and minimum-profit enforcement.
- Fork-result persistence: block, strategy, gas, predicted net, realized net,
  and model error.
- Existing v0.2 scanner remains read-only.

## Tests

Offline Python and CLI tests:

```bash
bash scripts/cli_test.sh
```

Compile the Solidity verifier:

```bash
forge build
```

Run the live-state fork smoke:

```bash
bash scripts/fork_test.sh
```

GitHub Actions runs Python tests and `forge build` for pushes/PRs. The real
Arbitrum fork smoke is available via **workflow_dispatch** because public RPC
availability is an external dependency.

## Current limitations

- v0.3 verifies the execution environment and Aave flash-loan lifecycle, but the
  existing arbitrage scanner is not yet automatically translated into Solidity
  swap calldata. That candidate-to-calldata bridge is the next subsystem.
- The fork smoke uses a local WETH faucet contract to provide enough extra WETH
  for the Aave premium; it proves borrowing/callback/repayment without pretending
  that the faucet is trading profit.
- No borrower indexer yet; liquidation discovery is still watchlist-based.
- No production executor, private-key handling, MEV bidding, or mainnet writes.
