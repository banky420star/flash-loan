# ⚡ ZERO Engine — v0.2 Shadow Mode

Zero-principal DeFi opportunity scanner. **Live Arbitrum One data in, decisions
recorded out — and nothing ever leaves your machine.** No wallet, no private
key, no signing, no broadcast. The ledger is the product: evidence of whether
an executable edge exists before any money is ever risked.

```
ARBITRUM ONE (read-only RPC)
        │
        ▼
 Aave V3 ── oracle prices, pool registry, account health (getUserAccountData)
 Uniswap V3 ── factory.getPool, slot0 + liquidity, exact single-range quotes
        │
        ▼
 SCANNERS ── arbitrage (size-swept 2-hop cycles) + liquidations (HF watchlist)
        │
        ▼
 RISK GATE ── minProfit = max(floor, k×gas, roi×size), stale rejection
        │
        ▼
 SQLITE LEDGER ── every candidate, PASS or REJECT, with reasons
```

## Run it

Double-click **`RUN_ZERO.command`** (Finder icon: ⚡) or:

```bash
python3 -m zero.cli doctor    # verify chain connectivity + contract registry
python3 -m zero.cli prices    # Aave oracle prices (reserves discovered on-chain)
python3 -m zero.cli hf 0x...  # health factor of any account
python3 -m zero.cli scan      # one shadow cycle, JSON output
python3 -m zero.cli shadow    # continuous shadow mode
python3 -m zero.cli ledger    # audit trail + stats
```

Tests: `python3 -m unittest discover -s tests`

## What's real in v0.2

- **Pure-Python keccak-256** — ABI selectors computed, not hardcoded.
  The permutation is verified against `hashlib.sha3_256` (same permutation,
  different padding); famous selectors (`a9059cbb` transfer) pin the padding.
- **Exact Uniswap V3 single-range swap math** in rational arithmetic
  (`fractions.Fraction`), derived from the virtual AMM
  `x = L/√P, y = L·√P` — no float error, and swaps that would cross a tick
  range are flagged `out_of_range` and rejected rather than misquoted.
- **Aave V3 registry resolution** — Pool and Oracle addresses are fetched
  live from `PoolAddressesProvider`, never hardcoded.
- **On-chain token discovery** — the reserve list and symbols come from the
  Pool registry, which caught two wrong hard-coded addresses during testing.
- **Arbitrage sizing** — geometric loan-size sweep per cycle; the scanner
  finds `q* = argmax P(q)` instead of guessing a fixed size.
- **Injectable RPC transport** — the whole engine runs offline against a
  fake chain, which is how 28 unit tests pass in under a second.

## What is deliberately NOT in v0.2

- No signer, no key handling, no transaction construction, no broadcasting.
- No borrower indexing (watchlist only — full event indexing is next stage).
- No fork simulation (Anvil stage), no executor contract, no MEV bidding.

## Honest limitations

- Single-range V3 quotes: large swaps cross ticks; the scanner flags rather
  than crosses. Fork simulation replaces this in v0.3.
- Liquidation P&L assumes a configurable close factor (0.5) and bonus (5%);
  real values come from reserve config in the contract stage.
- Gas is a configured estimate, not `eth_estimateGas`.
- Detection≠capture: shadow profits are *evidence*, never claimed P&L.

## Config

`config/arbitrum.json` — chain id, RPC URL, gate parameters, cycle
definitions, watchlist. Gate defaults: floor $2, 4× gas, 0.01% ROI.