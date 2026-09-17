# ZERO Live Production Design

Date: 2026-09-17
Status: Approved architecture, implementation pending

## Objective

Extend ZERO from a shadow/fork verifier into a guarded Arbitrum execution system that can detect, verify, submit, and reconcile profitable opportunities while preserving shadow mode as the default.

The system must optimize for realized net profit, not predicted profit. It must never treat a forecast, local quote, or fork result as wallet profit.

No design can guarantee profit. Production success means positive realized P&L over a statistically meaningful sample with bounded losses, stable execution, and reproducible accounting.

## Existing foundation

ZERO already provides:
- 20-worker pinned-block scanner and route leasing.
- Executable-only hot path and batched market reads.
- Exact-block fork verification.
- Adaptive model reserve learning.
- ExecutionPolicy risk gate.
- Hardened ZeroExecutor contract with allowlists, pause controls, max-loan controls, deadlines, minimum-output checks, and minimum-profit enforcement.
- Runtime heartbeat, SQLite ledger, control-floor TUI, and process TUI.

The existing runtime intentionally contains no signer or mainnet broadcast path. Live trading will be introduced as a separate controller rather than mixed into scanner internals.

## Operating modes

ZERO will expose three explicit modes:

1. `shadow` - current behavior. Read-only chain access, scan, fork verify, record simulated evidence. Default.
2. `paper` - run the full live controller and risk policy but stop before signing. Record the transaction that would have been submitted.
3. `live` - sign and submit only after every gate succeeds.

`live` requires both configuration and an explicit arm action. Restarting the process returns the controller to DISARMED unless the operator explicitly opts into persistent arming.

## Architecture

```text
Arbitrum heads / feed
        |
        v
Market State Ingestor
        |
        v
Executable Route Scanner
        |
        v
Exact Quote + Economics
        |
        v
Fork / eth_call Preflight
        |
        v
ExecutionPolicy
        |
        v
Live Controller -- DISARMED by default
        |
        v
Signer + Nonce Manager
        |
        v
Transaction Broadcaster
        |
        v
Receipt + P&L Reconciler
        |
        +--> Ledger / Adaptive Reserve / Kill State / TUI
```

## Market-state ingestion

Phase 1 uses WebSocket/new-head driven scans against a production-quality Arbitrum endpoint. A new head schedules one scan; overlapping scans are coalesced so stale work cannot backlog.

The scanner reuses static metadata caches but refreshes block-sensitive oracle prices and pool state at one pinned block. The existing executable-only route restriction remains in force.

The architecture leaves an interface for a later Arbitrum sequencer-feed ingestor. Arbitrum Nitro exposes a real-time sequencer feed; adopting it is a latency optimization after the live controller is proven, not a prerequisite for the first guarded release.

Public RPC endpoints remain suitable for fallback/diagnostics, not the primary production latency path.

## Route execution scope

Initial live scope is deliberately narrow:
- Uniswap V3 two-pool cycles already supported by the hardened executor.
- Only allowlisted tokens, pools/routers, selectors, and loan assets.
- Only routes that pass exact quote, preflight, policy, and minimum-profit gates.

Sushi V3 and Camelot remain observation-only until each has an audited/tested execution encoder and fork fixture. They must not enter the live opportunity book merely because quoting is supported.

## Candidate pipeline

A candidate may advance only if all stages succeed:

1. Current block/state is fresh enough for configured `max_block_lag`.
2. Local route model reports positive expected net.
3. Exact quote still reports positive net.
4. Expected net exceeds the production profit floor and adaptive reserve.
5. Gas estimate is within the configured cap.
6. Candidate age/deadline remains valid.
7. Exact-call/fork preflight succeeds using the same calldata intended for live execution.
8. `ExecutionPolicy.authorize()` returns allowed.
9. Candidate has not already been submitted for the same block/route/size.
10. Live controller is armed.

A failure at any stage records a rejection reason and never falls through to a weaker execution path.

## Production profit gate

Shadow mode may continue to admit tiny positive-net candidates for learning. Live mode uses a stricter gate:

```text
required_live_net_usd =
    max(
        configured_min_live_profit_usd,
        gas_usd * min_profit_to_gas_multiple,
        adaptive_reserve_usd + execution_buffer_usd
    )

candidate.expected_net_usd >= required_live_net_usd
```

The initial live defaults will be conservative and configurable. Increasing opportunity count must never bypass the gate.

## Signer boundary

Signer logic lives in a dedicated module/process interface. The scanner, TUI, and fork verifier never receive raw private-key material.

Supported first release:
- Local encrypted keystore or OS-backed signer configuration.
- Secret supplied through runtime environment/secure prompt, not committed `.env` files.
- Chain ID fixed to Arbitrum One for live mode.
- Executor contract address and expected bytecode/code hash validated at startup.

The controller refuses live startup if signer address, executor owner/caller authorization, chain ID, executor code, or configured allowlists do not match expectations.

## Nonce and transaction lifecycle

Exactly one nonce manager owns submissions for the configured signer.

States:
`DETECTED -> PREFLIGHTED -> AUTHORIZED -> SIGNED -> SUBMITTED -> MINED | REVERTED | DROPPED | REPLACED`

Rules:
- Serialize nonce reservation.
- Never reuse a nonce while a transaction is pending unless performing an explicit replacement of that transaction.
- Persist pending transaction metadata before broadcast acknowledgement is treated as complete.
- On restart, reconcile pending nonces and receipts before accepting new live work.
- Bound replacement attempts and replacement fee escalation.

## Broadcast and MEV handling

First production release uses a configurable authenticated Arbitrum RPC provider for transaction submission.

Broadcast interface is isolated so a protected/private transaction delivery provider can be added without changing scanner or policy code. No candidate is profitable merely because it was profitable before public submission; realized receipt data is authoritative.

## Reconciliation and realized P&L

For every mined transaction ZERO records:
- transaction hash and block.
- status/revert state.
- gas used and effective gas price.
- base-asset balance delta on the executor.
- flash premium paid.
- realized gross and realized net USD using the execution-block price basis.
- predicted vs realized model error.
- latency from detection through submission and inclusion.

Only realized on-chain balance change minus gas is counted as live P&L.

Successful and failed live outcomes feed the adaptive reserve model separately from fork evidence so simulated data can never be mistaken for live evidence.

## Risk controls

Required live controls:
- Global ARM / DISARM.
- Emergency KILL that blocks new authorization immediately.
- Contract-level pause support.
- Maximum block lag.
- Candidate deadline / maximum age.
- Maximum loan notional per asset.
- Maximum gas USD per transaction.
- Minimum live net USD.
- Minimum profit-to-gas multiple.
- Maximum consecutive reverts.
- Maximum modeled/live daily loss.
- Maximum pending transactions.
- Duplicate candidate suppression.
- Maximum replacement attempts.
- Optional single-transaction mode for first deployment.

Any breached hard limit disarms the controller and surfaces the reason in runtime status and TUI.

## TUI additions

Control floor will show:
- MODE: SHADOW / PAPER / LIVE.
- ARMED / DISARMED / KILLED.
- signer address availability (never secret material).
- executor address and validation state.
- latest chain head and lag.
- candidate count and production-gate rejects.
- pending transaction count and active nonce.
- last transaction hash/status.
- session/today/all-time live realized P&L.
- fork simulated P&L in a separate section.
- consecutive reverts and daily loss budget.

Color semantics:
- green: healthy/armed/positive realized state.
- yellow: degraded/stale/pending/warning.
- red: killed/revert/RPC/signing/policy failure.
- cyan: section/status headings.
- magenta: simulated P&L so it cannot be visually confused with live P&L.

## Configuration

New environment/config keys will include:
- `ZERO_MODE=shadow|paper|live`
- `ZERO_LIVE_ARM_REQUIRED=1`
- `ZERO_WS_URL`
- `ZERO_BROADCAST_RPC_URL`
- `ZERO_EXECUTOR_ADDRESS`
- `ZERO_SIGNER_BACKEND`
- signer-backend-specific keystore reference (never raw private key in repository files)
- live profit/risk limits
- receipt timeout and replacement limits

`.env.example` documents names and safe defaults only. Real secrets remain external.

## CLI

Planned commands:

```bash
python3 -m zero.cli doctor
python3 -m zero.cli live-doctor
python3 -m zero.cli swarm --interval 5          # shadow compatibility
python3 -m zero.cli controller --mode shadow
python3 -m zero.cli controller --mode paper
python3 -m zero.cli controller --mode live      # starts DISARMED
python3 -m zero.cli arm
python3 -m zero.cli disarm
python3 -m zero.cli kill --reason "operator"
python3 -m zero.cli reconcile
python3 -m zero.cli health
```

A one-click launcher may open the existing three-pane tmux dashboard, but it must not auto-arm live execution.

## Testing strategy

TDD is required for each new component.

Offline tests:
- state machine transitions.
- stale/deadline/profit/gas/loan/duplicate gates.
- nonce reservation and restart reconciliation.
- signer abstraction without real secrets.
- broadcaster retry/replacement classification.
- receipt accounting and realized-P&L math.
- mode/arm/kill enforcement.
- TUI live-vs-simulated separation.

Fork tests:
- hardened executor success.
- minimum-profit revert.
- allowlist rejection.
- stale/deadline rejection.
- approved loan bounds.
- deterministic receipt/P&L parsing.

Paper soak:
- run continuously against live Arbitrum state with signing disabled.
- no unhandled exceptions.
- no duplicate submissions.
- no stale candidate authorization.
- stable memory/ledger behavior.

Live rollout gate:
1. executor deployed and independently validated.
2. live-doctor fully green.
3. paper mode stable.
4. at least 100 measured fork outcomes with positive aggregate realized simulation evidence.
5. initial live cap restricted to a small operator-configured loan limit and one pending transaction.
6. manual ARM required.

## Acceptance criteria

The production slice is complete when:
- `shadow` remains backwards compatible.
- `paper` executes the entire live decision path without signing.
- `live` cannot submit while disarmed or killed.
- only policy-approved, preflighted candidates can reach the signer.
- nonce state survives restart and reconciles correctly.
- receipts produce auditable realized P&L.
- TUI clearly separates simulated and wallet P&L.
- all offline tests pass.
- Foundry build and hardened executor fork tests pass.
- a documented runbook explains shadow, paper, live-doctor, arm/disarm/kill, and reconciliation.

## Explicit non-goals for this slice

- Guaranteed profit.
- Unlimited leverage or uncapped loan sizes.
- Automatic strategy relaxation after losses.
- Auto-arming after restart.
- Raw private keys stored in the repository.
- Live Sushi/Camelot execution before their encoder/fork validation is complete.
- Sequencer-feed parsing in the first guarded production release; the interface is prepared for it as the next latency stage.
