# ZERO Engine v0.3 Fork Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add deterministic Arbitrum One fork verification with local-only write protection and a real Aave V3 flash-loan smoke test.

**Architecture:** `zero/fork.py` owns Anvil process construction, loopback-only write guards, and fork status. The CLI exposes fork diagnostics and a Foundry-backed fork test; Foundry contains a simulation-only flash-loan executor that replays protocol calls locally. Existing v0.2 scanning remains read-only.

**Tech Stack:** Python 3.10+ stdlib, Foundry/Anvil, Solidity 0.8.24, GitHub Actions.

**Spec:** Approved in chat on 2026-09-15: exact-block Arbitrum fork, no live writes, local simulation executor, gas/result evidence, preserve v0.2 scanner.

## Global Constraints

- Arbitrum One chain ID is 42161.
- No private keys in repository or configuration.
- No `eth_sendRawTransaction` or `eth_sendTransaction` may target a non-loopback RPC URL.
- Mainnet RPC remains read-only.
- Existing v0.2 scanner APIs remain backward compatible.
- Fork tests must fail explicitly when Foundry is required but unavailable; unit tests may run without Foundry.

---

### Task 1: Fork process and safety guard
- [x] Write tests for loopback recognition, remote-write rejection, exact-block Anvil command, and executable detection.
- [x] Verify RED because `zero.fork` did not exist.
- [x] Implement `zero/fork.py` minimally.
- [x] Run tests to green.

### Task 2: Simulation-only Solidity executor
- [x] Add standalone Solidity interfaces and `ZeroForkExecutor.sol`.
- [x] Add fork test using real Aave V3 `flashLoanSimple` on Arbitrum and a local WETH faucet step to cover premium.
- [x] Add `foundry.toml` with Solidity 0.8.24.
- [x] Add `scripts/fork_test.sh` that requires `forge` and an RPC URL.

### Task 3: CLI and CI integration
- [x] Add fork status, command, test, and ledger CLI commands.
- [x] Add offline unit tests for new fork helpers and persistence.
- [ ] Extend GitHub Actions with Python unit tests and Foundry build.
- [ ] Update README with v0.3 commands and safety boundary.
- [ ] Run complete branch verification.
