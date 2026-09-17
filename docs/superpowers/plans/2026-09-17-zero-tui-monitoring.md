# ZERO TUI Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two read-only curses dashboards for ZERO trading/fork observability, including P&L and process health.

**Architecture:** Runtime telemetry is expanded with an independent heartbeat, while a new read-only data layer aggregates status JSON, SQLite fork/opportunity rows, process stats, and log tails. Two curses views render the same snapshot model without calling RPC or mutating engine state.

**Tech Stack:** Python 3.13 standard library, curses, sqlite3, subprocess, JSON.

**Spec:** `docs/superpowers/specs/2026-09-17-zero-tui-monitoring-design.md`

## Global Constraints
- Read-only monitoring only; no signer or broadcast path.
- No third-party Python dependency.
- TUI never performs chain RPC.
- P&L is labelled fork/shadow simulation evidence.
- Closing a TUI must not terminate the swarm.

---

### Task 1: Long-cycle runtime heartbeat
**Files:** modify `zero/runtime.py`, `zero/cli.py`; test `tests/test_runtime_progress.py`.
**Produces:** RuntimeStatus cycle/process fields; `record_cycle_start`, `heartbeat`, and background CLI heartbeat.
- [ ] Write tests proving heartbeat can update during a running cycle and process/cycle fields serialize.
- [ ] Run targeted tests and verify RED.
- [ ] Implement the minimal thread-safe runtime methods and CLI heartbeat helper.
- [ ] Run targeted tests and full runtime tests GREEN.
- [ ] Commit.

### Task 2: Read-only monitoring data model
**Files:** create `zero/tui_data.py`; test `tests/test_tui_data.py`.
**Produces:** `MonitoringSnapshot`, `load_monitoring_snapshot`, P&L aggregation, process/log/status readers.
- [ ] Write fixtures for measured success, execution revert, invalid harness, status JSON and log rows.
- [ ] Verify RED because the module is absent.
- [ ] Implement read-only SQLite/status/process/log collection and simulated P&L aggregation.
- [ ] Verify targeted tests GREEN.
- [ ] Commit.

### Task 3: Control Floor TUI
**Files:** create `zero/tui.py`; test `tests/test_tui_render.py`.
**Produces:** pure text render helpers plus curses `control` mode.
- [ ] Write tests for header, P&L labels, strategy split, recent fork rows and stale-cycle messaging.
- [ ] Verify RED.
- [ ] Implement deterministic render helpers and curses refresh loop.
- [ ] Verify targeted tests GREEN.
- [ ] Commit.

### Task 4: Process TUI and launchers
**Files:** modify `zero/tui.py`; create `scripts/zero_tui.sh`, `scripts/zero_process_tui.sh`; modify `scripts/zero_dev.sh`; test `tests/test_tui_launchers.py`.
**Produces:** process mode and one-command launchers.
- [ ] Write tests for launcher safety and CLI command availability.
- [ ] Verify RED.
- [ ] Implement process view and launch scripts; add `tui`/`process-tui` dev launcher modes.
- [ ] Verify targeted tests GREEN.
- [ ] Commit.

### Task 5: Final verification and docs
**Files:** modify `README.md`.
- [ ] Run complete Python/CLI suite.
- [ ] Run Forge build.
- [ ] Smoke-test both TUIs in non-interactive render mode.
- [ ] Document startup and monitoring commands.
- [ ] Commit, review diff, and finish branch.
