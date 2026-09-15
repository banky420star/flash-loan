# ZERO Engine v0.4 Candidate Calldata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert a PASS Arbitrum arbitrage candidate into deterministic SwapRouter02 calldata and replay the complete Aave flash-loan + two-swap route on an exact-block fork.

**Architecture:** Extend the existing scanner to retain both hop outputs and live Aave flash premium, normalize a candidate object, encode ERC20 approval plus two SwapRouter02 `exactInputSingle` calls, then feed those steps into the existing `ZeroForkExecutor` on an Arbitrum fork. Keep all writes local-only and record realized-vs-predicted evidence.

**Tech Stack:** Python 3.11 stdlib, repository Keccak/ABI helpers, Solidity 0.8.24, Foundry/Anvil, Arbitrum One, Aave V3, Uniswap V3 SwapRouter02.

**Spec:** `docs/superpowers/specs/2026-09-15-v04-candidate-calldata-design.md`

## Global Constraints

- Arbitrum One chain id is `42161`.
- SwapRouter02 is `0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45`.
- No private keys, production signer, mainnet broadcaster, or remote write path.
- Write-capable RPC targets must remain loopback-only.
- No new Python package dependency.
- Candidate fork block must match candidate detection block.
- Aave flash premium must be queried from `FLASHLOAN_PREMIUM_TOTAL()` and included in predicted net.
- Second swap minimum output must cover principal, flash premium, and candidate minimum profit.

---

### Task 1: Preserve swap outputs and account for Aave flash premium

**Files:**
- Modify: `zero/aave.py`
- Modify: `zero/strategies/arbitrage.py`
- Modify: `zero/engine.py`
- Test: `tests/test_rpc_aave.py`
- Test: `tests/test_arbitrage.py`

**Interfaces:**
- Produces: `AaveV3.flashloan_premium_total(pool: str) -> int`
- Produces: profit-curve rows with `hop1_out` and `hop2_out`
- Produces: engine cycle rows with `flash_premium_bps`, `flash_fee`, and premium-adjusted `net`

- [ ] **Step 1: Write failing premium and profit-curve tests.**

```python
def test_flashloan_premium_total(self):
    premium = self.aave.flashloan_premium_total(self.pool)
    self.assertEqual(premium, 5)


def test_curve_preserves_hop_outputs(self):
    row = self._cycle().profit_curve(state_a, state_b, [1000])[0]
    self.assertGreater(row["hop1_out"], 0)
    self.assertGreater(row["hop2_out"], 0)
    self.assertAlmostEqual(row["gross"], row["hop2_out"] - row["size"])
```

- [ ] **Step 2: Run targeted tests and confirm RED.**

Run: `python3 -m unittest tests.test_rpc_aave tests.test_arbitrage -v`

Expected: failures because `flashloan_premium_total`, `hop1_out`, and `hop2_out` do not yet exist.

- [ ] **Step 3: Implement premium query and retain hop outputs.**

```python
def flashloan_premium_total(self, pool: str) -> int:
    raw = self.rpc.eth_call(pool, selector_hex("FLASHLOAN_PREMIUM_TOTAL()"))
    return decode_uints(raw)[0]
```

Update curve rows to include `hop1_out` and `hop2_out`. In `ShadowEngine.scan_arbitrage`, compute `flash_fee = best["size"] * premium_bps / 10_000` and use `net = gross - gas_usd - flash_fee`.

- [ ] **Step 4: Run targeted tests and confirm GREEN.**

Run: `python3 -m unittest tests.test_rpc_aave tests.test_arbitrage -v`

- [ ] **Step 5: Run full Python suite.**

Run: `python3 -m unittest discover -s tests -v`

---

### Task 2: Candidate model and static ABI calldata encoder

**Files:**
- Create: `zero/candidate.py`
- Create: `zero/calldata.py`
- Modify: `config/arbitrum.json`
- Test: `tests/test_candidate.py`
- Test: `tests/test_calldata.py`

**Interfaces:**
- Produces: `ArbitrageCandidate`
- Produces: `ExecutionStep(target: str, value: int, data: str)`
- Produces: `build_uniswap_v3_steps(candidate, router, slippage_bps) -> list[ExecutionStep]`

- [ ] **Step 1: Write failing candidate/calldata tests.**

Tests must assert:

```python
self.assertEqual(candidate.loan_amount_raw, 1_000_000_000)
self.assertEqual(steps[0].data[:10], selector_hex("approve(address,uint256)"))
self.assertEqual(steps[1].target.lower(), SWAP_ROUTER_02.lower())
self.assertEqual(decoded_leg1.amount_in, candidate.loan_amount_raw)
self.assertEqual(decoded_leg1.recipient, "0x0000000000000000000000000000000000000002")
self.assertEqual(decoded_leg2.amount_in, 0)
self.assertEqual(decoded_leg2.recipient, "0x0000000000000000000000000000000000000001")
self.assertGreaterEqual(decoded_leg2.amount_out_minimum,
                        candidate.loan_amount_raw + candidate.flash_fee_raw + candidate.min_profit_raw)
```

- [ ] **Step 2: Run tests and confirm RED.**

Run: `python3 -m unittest tests.test_candidate tests.test_calldata -v`

- [ ] **Step 3: Implement immutable candidate dataclass and encoders.**

Use static ABI words only. Encode the SwapRouter02 signature:

`exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))`

The first leg uses recipient `address(2)`; the second uses recipient `address(1)` and `amountIn = 0`.

- [ ] **Step 4: Add config.**

```json
"execution": {
  "swap_router_02": "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
  "slippage_bps": 20
}
```

inside `arbitrage`.

- [ ] **Step 5: Run targeted and full tests.**

Run: `python3 -m unittest tests.test_candidate tests.test_calldata -v`

Run: `python3 -m unittest discover -s tests -v`

---

### Task 3: Normalize PASS candidates from ShadowEngine and expose CLI

**Files:**
- Modify: `zero/engine.py`
- Modify: `zero/cli.py`
- Test: `tests/test_candidate_engine.py`
- Test: `tests/test_candidate_cli.py`

**Interfaces:**
- Produces: cycle rows containing serializable `candidate` for PASS arbitrage only
- Produces CLI commands `candidate` and `calldata`

- [ ] **Step 1: Write failing engine/CLI tests.**

Assert rejected opportunities have no executable candidate, PASS opportunities expose candidate raw amounts and fee tiers, and `zero calldata` prints exactly three ordered steps.

- [ ] **Step 2: Run targeted tests and confirm RED.**

Run: `python3 -m unittest tests.test_candidate_engine tests.test_candidate_cli -v`

- [ ] **Step 3: Implement candidate normalization using the exact scanner block/state outputs.**

Do not re-quote inside the candidate bridge. Build from `best` plus current cycle configuration and Aave premium.

- [ ] **Step 4: Add CLI commands.**

`candidate` runs one scan and outputs PASS candidates as JSON. `calldata` emits the three execution steps for the highest predicted-net PASS candidate.

- [ ] **Step 5: Run full Python suite.**

Run: `bash scripts/cli_test.sh`

---

### Task 4: Execute the generated route on a real Arbitrum fork

**Files:**
- Create: `contracts/test/ZeroCandidateArbFork.t.sol`
- Modify: `contracts/src/ZeroForkExecutor.sol` only if required by a failing fork test
- Create: `scripts/candidate_fork_test.sh`
- Test: `tests/test_candidate_fork_layout.py`

**Interfaces:**
- Consumes: Aave V3 pool, USDC, WETH, SwapRouter02, fee tiers 500 and 3000
- Produces: real two-swap Aave flash-loan replay result

- [ ] **Step 1: Add structural test first.**

Assert fork test references the canonical SwapRouter02, Aave provider, USDC, WETH, both fee tiers, and `ZeroForkExecutor.run`.

- [ ] **Step 2: Run Python structural test and confirm RED.**

Run: `python3 -m unittest tests.test_candidate_fork_layout -v`

- [ ] **Step 3: Add Foundry fork test using real contracts.**

The test must:
- resolve Aave Pool from provider;
- deploy `ZeroForkExecutor`;
- source only the flash-premium seed if necessary;
- create approve + leg1 + leg2 `Step[]`;
- use real SwapRouter02 and pools;
- execute Aave `flashLoanSimple`;
- assert principal/premium repaid and final USDC profit is at least the requested minimum.

No artificial USDC trading profit may be transferred into the executor.

- [ ] **Step 4: Compile.**

Run: `forge build`

- [ ] **Step 5: Run the real fork test.**

Run: `bash scripts/candidate_fork_test.sh`

Expected: one real Aave + Uniswap round-trip fork test passes, or the test explicitly reports that current market state has no profitable configured round trip without weakening `minProfit`.

---

### Task 5: CI, evidence, and documentation

**Files:**
- Modify: `.github/workflows/test.yml`
- Modify: `README.md`
- Modify: `zero/ledger.py` only if additional result detail support is needed

**Interfaces:**
- CI runs Python tests and `forge build` on every PR
- real candidate fork replay runs on the v0.4 PR and workflow dispatch

- [ ] **Step 1: Update CI with candidate fork smoke.**

Use the public Arbitrum RPC only as a fork source; no live writes.

- [ ] **Step 2: Document v0.4 commands and invariants.**

Include `candidate`, `calldata`, and `fork-arb` usage plus current limitations.

- [ ] **Step 3: Run final verification.**

Run: `bash scripts/cli_test.sh`

Run: `forge build`

Run: `bash scripts/candidate_fork_test.sh`

- [ ] **Step 4: Open PR against `main` and require all checks green before merge.**
