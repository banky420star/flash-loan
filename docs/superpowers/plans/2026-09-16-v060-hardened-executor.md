# ZERO v0.6.0 Hardened Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed, allowlisted fork-tested executor and off-chain execution policy without enabling mainnet signing or broadcast.

**Architecture:** Keep `ZeroForkExecutor.sol` unchanged for generic simulation. Add a separate `ZeroExecutor.sol` with explicit router/token/selector allowlists and strict state guards, plus a pure Python execution policy for duplicate/risk gating. The repository remains fork-only: no private key input, signer integration, or remote broadcast path is added.

**Tech Stack:** Solidity 0.8.24, Foundry, Python 3.11.

**Spec:** `docs/superpowers/specs/2026-09-16-zero-roadmap-completion-design.md`

## Global Constraints
- No arbitrary target call.
- No private-key configuration or remote transaction broadcast.
- Contract must authenticate Aave callback sender and initiator.
- Only allowlisted assets, routers, and function selectors may execute.
- Maximum step count, maximum loan amount, deadline, and minimum profit are enforced on-chain.

---

### Task 1: Hardened executor contract
**Files:** Create `contracts/src/ZeroExecutor.sol`; Create `contracts/test/ZeroExecutor.t.sol`.
**Interfaces:** owner-managed allowlists, `run(...)`, Aave `executeOperation(...)`, `pause()`/`unpause()`.
- [ ] Write failing Foundry tests for unauthorized pool/initiator, paused execution, disallowed token/router/selector, excessive steps, excessive loan, expired deadline, approval reset, and minimum-profit failure.
- [ ] Run targeted Foundry tests and confirm RED because `ZeroExecutor` does not exist.
- [ ] Implement minimal typed executor with explicit selector checks, no generic unrestricted targets, bounded approvals, callback guards, and reentrancy state.
- [ ] Re-run targeted tests and commit `feat: add hardened fork-only executor`.

### Task 2: Execution policy
**Files:** Create `zero/execution_policy.py`; Test `tests/test_execution_policy.py`.
**Interfaces:** `ExecutionPolicy.evaluate(candidate, state) -> PolicyDecision` and duplicate key `(block, candidate_id)`.
- [ ] Write RED tests for duplicate suppression, stale block/deadline, max gas, max loan/notional, max consecutive reverts, max daily modeled loss, and emergency kill.
- [ ] Implement pure deterministic policy objects with no signer or network side effects.
- [ ] Confirm GREEN and commit `feat: add execution coordinator policy`.

### Task 3: Fork integration only
**Files:** Create `contracts/test/ZeroExecutorFork.t.sol`; Modify `README.md`; Test `tests/test_no_live_broadcast.py`.
**Interfaces:** deterministic fork replay can invoke `ZeroExecutor`; CLI still has no live-execute command.
- [ ] Add a fork test for an allowlisted Aave + Uniswap candidate path and a static Python test asserting no private-key environment names, signer imports, raw-transaction submission, or `eth_sendRawTransaction` code exists in `zero/`.
- [ ] Run `bash scripts/cli_test.sh`, `forge build`, existing fork smokes, and hardened-executor fork smoke.
- [ ] Document the explicit production gap: external signer/broadcast integration is intentionally out of scope.
- [ ] Commit `test: verify hardened executor remains fork-only`.