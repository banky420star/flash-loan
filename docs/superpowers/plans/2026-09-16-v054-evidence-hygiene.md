# ZERO v0.5.4 Evidence Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent infrastructure and harness failures from contaminating adaptive reserve learning.

**Architecture:** Add explicit fork outcome classes to `ForkResult`, persist them in SQLite with an idempotent migration, and filter reserve-learning history to measured execution evidence only.

**Tech Stack:** Python 3.11, sqlite3, unittest, Foundry.

**Spec:** `docs/superpowers/specs/2026-09-16-zero-roadmap-completion-design.md`

## Global Constraints
- Shadow/fork only; no signer or remote writes.
- Existing fork ledger rows remain readable.
- Reserve samples include only `measured_success` and `execution_revert`.

---

### Task 1: Outcome classification type
**Files:** Modify `zero/fork.py`; Test `tests/test_fork_outcome_class.py`.
**Interfaces:** `ForkResult(..., outcome_class: str = "measured_success")`; `reserve_eligible` property.
- [ ] Write tests asserting allowed classes, success/class consistency, and reserve eligibility.
- [ ] Run `python3 -m unittest tests.test_fork_outcome_class -v`; expect RED before implementation.
- [ ] Implement constants `measured_success`, `execution_revert`, `infrastructure_error`, `invalid_harness`, validate in `__post_init__`, and expose `reserve_eligible`.
- [ ] Re-run targeted tests and commit `feat: classify fork outcomes`.

### Task 2: Classify fork runner failures
**Files:** Modify `zero/fork_cli.py`; Test `tests/test_fork_outcome_runner.py`.
**Interfaces:** `run_live_candidate_fork_result()` returns explicit outcome class.
- [ ] Add fixtures for missing Forge (`returncode=2`/tooling message), RPC/network failure, strategy revert (`StepFailed`/`MinimumProfitNotMet`), missing result file, and success.
- [ ] Confirm RED.
- [ ] Classify subprocess output deterministically: strategy reverts -> `execution_revert`; environment/network/process errors -> `infrastructure_error`; missing/malformed result output -> `invalid_harness`; successful result JSON -> `measured_success`.
- [ ] Confirm GREEN and commit `fix: separate execution evidence from infrastructure failures`.

### Task 3: Persist and filter evidence
**Files:** Modify `zero/ledger.py`, `zero/reserve.py`; Test `tests/test_fork_evidence_ledger.py`.
**Interfaces:** `fork_verifications.outcome_class`; `Ledger.fork_economics(..., reserve_eligible_only=True)`.
- [ ] Add a failing migration/history test using a pre-v0.5.4 database.
- [ ] Implement idempotent `ALTER TABLE` migration when `outcome_class` is absent and persist new results.
- [ ] Make `fork_economics` return outcome class and exclude non-eligible rows by default for reserve learning; legacy rows default to `execution_revert` only when they have evidence of a launched strategy revert, otherwise `invalid_harness`.
- [ ] Run `bash scripts/cli_test.sh` and `forge build`; commit `feat: train reserve only on measured fork evidence`.