# ZERO TUI Monitoring Design

## Goal
Provide two read-only terminal dashboards that make the shadow/fork engine observable without adding latency or trading authority to the execution path.

## Control Floor
The control TUI shows engine/process liveness, current/last cycle state, block/head lag, route and worker counts, RPC endpoint, recent opportunities, recent fork outcomes, error events, adaptive reserve, and a dedicated fork/shadow P&L panel.

P&L is explicitly simulation evidence, not wallet P&L. It includes predicted net, realized fork net, model error, session/day totals, per-strategy totals, pass/revert/harness counts, best/worst measured result, and a compact cumulative realized sparkline.

## Process Monitor
The process TUI shows the ZERO swarm PID, uptime, CPU, memory, process state, engine heartbeat age, cycle age/phase, status-file freshness, SQLite WAL files, RPC endpoint, recent errors, and log tail. It distinguishes process liveness from heartbeat freshness so a long scan is not reported as a dead process.

## Runtime telemetry
RuntimeStatus gains process_pid, process_started_at, cycle_started_at, cycle_phase, cycle_number, and last_cycle_completed_at. A lightweight daemon heartbeat in the CLI updates heartbeat_at during a long scan. The trading engine does not read these fields; telemetry remains one-way.

## Data sources
- run/zero-status.json: engine heartbeat and cycle metadata
- zero_ledger.db: opportunities and fork verification evidence
- run/swarm.log: error/event tail
- ps on macOS/Linux: process CPU, memory, state and uptime

## Safety
The TUIs are read-only. They expose no signer inputs, no transaction broadcast command, and no arbitrary process-control hotkeys. Closing either TUI never stops the swarm.

## Implementation constraints
- Python standard library only; curses for rendering.
- Works on macOS Terminal and standard Linux terminals.
- SQLite opened read-only where possible and never migrated by the TUI.
- Refresh defaults to 1 second and never calls chain RPC directly.
- Rendering logic is separated from data collection so unit tests do not require a terminal.

## Commands
- python3 -m zero.tui control
- python3 -m zero.tui process
- ./scripts/zero_tui.sh
- ./scripts/zero_process_tui.sh
