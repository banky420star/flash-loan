# ZERO v0.5.7 Liquidation Swarm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder watchlist economics with state-aware Aave liquidation candidates that can be ranked beside arbitrage candidates.

**Architecture:** Separate borrower indexing, liquidation state/economics, collateral unwind routing, and fork payload construction. The first implementation supports configured/indexed borrowers and exact pinned state; it does not claim mempool priority or guaranteed capture.

**Tech Stack:** Python 3.11, Aave V3 JSON-RPC, venue route graph, Foundry fork tests.

**Spec:** `docs/superpowers/specs/2026-09-16-zero-roadmap-completion-design.md`

## Global Constraints
- Health factor < 1 is required but not sufficient.
- Close factor and bonus cannot be hard-coded admission assumptions.
- Candidate must cover debt repayment, flash premium, gas, reserve, and collateral unwind.

---

### Task 1: Liquidation state model
**Files:** Create `zero/liquidations.py`; Modify `zero/aave.py`; Test `tests/test_liquidation_state.py`.
**Interfaces:** `LiquidationState`, `LiquidationCandidate`, `build_liquidation_state(aave, borrower, block)`.
- [ ] RED tests cover safe borrower, HF < 1, 50% close-factor band, full-close conditions, and configured reserve liquidation bonus/protocol fee inputs.
- [ ] Add pinned Aave reserve-configuration/debt/collateral reads needed for deterministic economics.
- [ ] Implement current close-factor rules using explicit thresholds/config rather than constants in `strategies/liquidation.py`.
- [ ] Confirm GREEN and commit `feat: model pinned Aave liquidation state`.

### Task 2: Candidate economics and unwind
**Files:** Create `zero/liquidation_quote.py`; Test `tests/test_liquidation_quote.py`.
**Interfaces:** `quote_liquidation(state, repay_asset, collateral_asset, route, block) -> LiquidationCandidate`.
- [ ] Write tests for collateral received after bonus/protocol fee, exact unwind quote, flash premium, gas/reserve subtraction, and zero/negative rejection.
- [ ] Implement Decimal/raw-unit economics and require an executable exact unwind route.
- [ ] Confirm GREEN and commit `feat: price flash-funded liquidation candidates`.

### Task 3: Swarm and fork integration
**Files:** Modify `zero/pnl.py`, `zero/cli.py`; Create `contracts/test/ZeroLiquidationFork.t.sol`; Test `tests/test_liquidation_swarm.py`.
**Interfaces:** liquidation candidates share ranking fields with opportunity book and use strategy `swarm_liquidation`.
- [ ] Add tests for common ranking, duplicate borrower suppression and worker failure isolation.
- [ ] Add a liquidation scan phase driven by configured/indexed borrower set; do not RPC-scan all addresses per block.
- [ ] Add deterministic fork fixture exercising `liquidationCall`, collateral unwind and Aave repayment.
- [ ] Run CLI/unit, `forge build`, Aave fork smoke and liquidation fork smoke; commit `feat: integrate liquidation swarm`.