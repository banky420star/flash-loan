# ⚡ ZERO Engine — v0.5 Swarm Fork Verifier

ZERO Engine is an Arbitrum One opportunity scanner with a **local-only fork
execution verifier**. v0.5 expands the v0.4 single-route scanner into one CEO
supervisor coordinating four managers and 20 concurrent scanning workers while
keeping the production safety boundary unchanged: **no private keys, no signer,
and no mainnet transaction broadcast path**.

```text
ARBITRUM ONE (read-only RPC)
        │
        ▼
  CEO pins block N
        │
        ├──────────────┬──────────────┬──────────────┐
        ▼              ▼              ▼              ▼
      ALPHA          BRAVO          CHARLIE         DELTA
     5 workers       5 workers       5 workers       5 workers
        │              │              │              │
        └──────────────┴──────┬───────┴──────────────┘
                              ▼
                    shared opportunity book
                              │
                    expected net > $0 only
                              │
                              ▼
                    exact three-step calldata
                              │
                              ▼
                    exact-block Foundry fork
                              │
                    Aave + Uniswap execution
                              │
                   repay + profit assertion
```

## Safety boundary

v0.5 remains a verification system, not a live mainnet trader.

- No private keys.
- No production signer.
- No nonce manager.
- No mainnet transaction broadcaster.
- No `eth_sendRawTransaction` path to Arbitrum One.
- Write-capable RPC targets remain loopback-only in `zero/fork.py`.
- The public Arbitrum RPC is used only for reads and Foundry fork state.
- The fork executor remains simulation-only.
- A positive scanner candidate is sent only to an exact-block local fork.
- A worker failure does not authorize a fallback transaction or bypass a gate.

## v0.5 swarm model

The default topology is configuration-driven:

```text
ZERO CEO
├── ALPHA   A1-A5   stable / ETH routes
├── BRAVO   B1-B5   BTC / ETH / stable routes
├── CHARLIE C1-C5   liquid-staking/restaking routes
└── DELTA   D1-D5   ARB/LINK/AAVE/GHO + overflow
```

The 20 scanner workers are concurrent tasks inside one supervisor. Managers and
the CEO are coordination roles, not extra scanner processes. Idle workers may
steal unleased route work from another queue so capacity is not wasted.

Every route has a canonical ID derived from chain, token pair, both pools, both
fee tiers, and direction. A per-block lease registry prevents two workers from
owning the same route at the same time.

### Exact-block consistency

For each cycle the CEO freezes one Arbitrum block `N`. Aave, oracle, token
metadata, Uniswap factory discovery, `slot0()`, and liquidity reads all use that
explicit block tag. Candidates carrying mixed-block state are rejected.

This is important because a false price discrepancy can otherwise appear when
one pool is read at block `N` and another at `N+1`.

## Profit rule

Swarm mode does **not** use the legacy `$2` minimum, `4x gas` minimum, or a
fixed minimum ROI. The admission rule is simply:

```text
expected_net_usd > 0
```

where:

```text
expected_net_usd
= quoted round-trip gross profit
- Aave flash-loan premium
- estimated gas
- configured model/slippage reserve
```

Swap fees and modeled price impact are already represented in the round-trip
quote and are not subtracted a second time.

A very small positive expected net can therefore remain eligible in shadow/fork
mode. Zero or negative expected net is always rejected.

Loan-size search is configured in USD. For non-stable base assets ZERO uses the
pinned Aave oracle price to convert the USD ladder into base-token quantities,
then ranks sizes by **expected net USD**, not by largest loan size or gross
profit.

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

## Swarm commands

Run one complete 20-worker scan cycle:

```bash
python3 -m zero.cli swarm-once
```

The JSON result includes:

```text
block
active_workers
routes_scanned
worker_failures
detected
positive_net
duplicates_suppressed
best_expected_net
fork_verifications_attempted
fork_verifications_passed
fork_verifications_failed
elapsed_s
candidates
```

Run continuously:

```bash
python3 -m zero.cli swarm --interval 5
```

This command continually scans and reports concise per-cycle metrics. If a
positive-net candidate is found, ZERO encodes the same constrained three-step
SwapRouter02 payload used by v0.4 and sends it to the exact-block local fork
verifier. Fork concurrency defaults to one so local Foundry replays do not
saturate the machine.

Stop continuous mode with `Ctrl-C`.

## Legacy / inspection commands

Run one legacy shadow scan:

```bash
python3 -m zero.cli scan
```

Show current legacy PASS arbitrage candidates:

```bash
python3 -m zero.cli candidate
```

Run the legacy shadow loop:

```bash
python3 -m zero.cli shadow --interval 30
```

Inspect the opportunity ledger:

```bash
python3 -m zero.cli ledger --tail 20
```

## Candidate → calldata

Print the executable representation of the highest predicted-net legacy PASS
candidate:

```bash
python3 -m zero.cli calldata
```

The current executable bridge is intentionally restricted to a two-pool
Uniswap V3 round trip:

```text
1. base token approve(SwapRouter02, loanAmount)
2. SwapRouter02.exactInputSingle(base → quote, fee A)
3. SwapRouter02.exactInputSingle(quote → base, fee B)
```

Canonical Arbitrum SwapRouter02:

```text
0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45
```

The first swap sends the intermediate token to router custody using Uniswap's
`address(2)` recipient sentinel. The second uses `amountIn = 0`, Uniswap's
contract-balance sentinel, so it consumes the exact intermediate balance and
returns the base token to the executor.

The final output must cover:

```text
flash principal
+ live Aave flash premium
+ base-token minimum profit
```

For swarm candidates the executable minimum profit includes the modeled gas and
reserve converted back into base-token units plus one raw base-token quantum.

## Exact candidate fork replay

The v0.4 single-route command remains available:

```bash
python3 -m zero.cli fork-arb
```

It performs:

```text
fresh live scan
→ choose highest predicted-net PASS candidate
→ encode exact SwapRouter02 calls
→ use detection block as FORK_BLOCK
→ replay through ZeroForkExecutor
→ require Aave repayment and minimum profit
```

If no PASS candidate exists it returns `no_pass_candidate` and makes no
execution attempt.

Inspection-only commands:

```bash
python3 -m zero.cli fork-command --block 505482254
python3 -m zero.cli fork-test --block 505482254
python3 -m zero.cli fork-ledger --tail 20
```

## Deterministic integration smoke

`candidate_fork_test.sh` deliberately creates a price discrepancy **only inside
the fork** and proves that the execution plumbing can:

1. borrow through real Aave V3 `flashLoanSimple`;
2. swap through a real Uniswap V3 pool;
3. swap the exact intermediate balance through the second pool;
4. repay principal plus the live Aave premium; and
5. retain at least the configured minimum profit.

This is an execution-path test, not evidence that a natural live-market edge
currently exists.

Run it directly:

```bash
bash scripts/candidate_fork_test.sh
```

## Tests

Offline Python + CLI suite:

```bash
bash scripts/cli_test.sh
```

Compile Solidity verification contracts:

```bash
forge build
```

Baseline Aave fork smoke:

```bash
bash scripts/fork_test.sh
```

Aave + Uniswap candidate fork smoke:

```bash
bash scripts/candidate_fork_test.sh
```

GitHub Actions runs the Python suite and Solidity build on pushes. Pull-request
validation also runs the real Aave fork smoke and deterministic Aave+Uniswap
candidate fork smoke.

## What changed in v0.5

- One CEO supervisor coordinates four managers and 20 workers.
- Worker topology is configuration-driven.
- Uniswap V3 pool discovery scans configured fee tiers for each assigned pair.
- All candidate state reads are pinned to one exact Arbitrum block.
- Route IDs and per-block leases suppress duplicate scanning.
- Work stealing lets idle workers consume unleased routes.
- Token metadata and pool state are cached in one immutable scan context.
- Loan sizes are configured in USD and converted using pinned oracle prices.
- Swarm candidates are ranked by expected net USD.
- Any modeled net profit strictly greater than zero can be admitted in
  shadow/fork mode.
- Worker failures are isolated rather than aborting the whole cycle.
- SQLite opportunity writes are serialized on the supervisor thread.
- Positive candidates are encoded into exact v0.4 calldata and sent to the
  local exact-block fork verifier.
- `swarm-once` and `swarm --interval N` commands expose the runtime.

## Current limitations / next stages

- v0.5 swarm execution is still **Uniswap V3 two-pool** only; multi-DEX and
  arbitrary multi-hop execution are not in this slice.
- The fast Uniswap quote model is single-range; exact fork replay remains the
  execution authority.
- Fork verification currently records success/failure at the swarm level, but
  automatic parsing of realized token P&L and gas from Foundry back into the
  `fork_verifications` ledger is the next subsystem.
- The adaptive safety reserve is not learned yet; `model_reserve_usd` is still
  configuration-driven.
- Liquidation discovery is still watchlist-based, not a complete borrower
  event index.
- Always-on VPS/service packaging has not been added yet.
- The generic `ZeroForkExecutor` remains simulation-only. A separate hardened
  production executor with allowlists, bounded steps, signer/nonce controls,
  and kill switches is required before any restricted mainnet stage.
- No production execution, private-key handling, MEV bidding, or mainnet writes
  are present.

## Always-on shadow/fork service

v0.5.8 adds restart-safe service packaging without adding a signer or a
mainnet broadcast path. Runtime state remains chain-derived; the JSON heartbeat
is observability only.

Local development:

```bash
cp .env.example .env
./scripts/zero_dev.sh doctor
./scripts/zero_dev.sh once
./scripts/zero_dev.sh run
./scripts/zero_dev.sh health
```

`ZERO_RPC_URLS` accepts an ordered comma-separated RPC pool. Transport and
rate-limit failures cool the failing endpoint and fall through to the next
endpoint; JSON-RPC contract/application errors do not fail over.

Docker/systemd assets live under `docker/`. Persistent SQLite data belongs in
`/var/lib/zero`; the heartbeat is written atomically to `/run/zero/status.json`
under systemd. The ledger uses WAL mode and a busy timeout. `python3 -m zero.cli
health` returns non-zero for a missing/stale heartbeat or an explicit kill
state. No private-key, mnemonic, signer, nonce-manager, or raw-transaction
configuration is part of this service release.
