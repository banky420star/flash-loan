from pathlib import Path
import unittest
from unittest.mock import patch

from zero.calldata import SWAP_ROUTER_02, build_uniswap_v3_steps
from zero.candidate import ArbitrageCandidate
from zero.fork_cli import run_live_candidate_fork


USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"


def payload():
    candidate = ArbitrageCandidate(
        block=505_482_254,
        name="live-pass",
        base_asset=USDC,
        quote_asset=WETH,
        base_decimals=6,
        quote_decimals=18,
        loan_size=1000.0,
        hop1_expected_out=0.30,
        hop2_expected_out=1010.0,
        fee1=500,
        fee2=3000,
        flash_premium_bps=5,
        gas_cost_usd=1.0,
        gross_profit=10.0,
        predicted_net=8.5,
        min_profit=2.0,
    )
    steps = build_uniswap_v3_steps(candidate, SWAP_ROUTER_02, 20)
    return {
        "candidate": candidate.as_dict(),
        "steps": [step.as_dict() for step in steps],
    }


class Completed:
    returncode = 0


class TestLiveCandidateFork(unittest.TestCase):
    @patch("zero.fork_cli.subprocess.run", return_value=Completed())
    def test_passes_exact_candidate_block_and_calldata_to_foundry(self, run):
        p = payload()
        rc = run_live_candidate_fork("https://arb1.arbitrum.io/rpc", p)
        self.assertEqual(rc, 0)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["bash", "scripts/live_candidate_fork_test.sh"])
        env = kwargs["env"]
        self.assertEqual(env["FORK_BLOCK"], str(p["candidate"]["block"]))
        self.assertEqual(env["ZERO_ASSET"].lower(), USDC.lower())
        self.assertEqual(env["ZERO_LOAN_RAW"], str(p["candidate"]["loan_amount_raw"]))
        self.assertEqual(env["ZERO_MIN_PROFIT_RAW"], str(p["candidate"]["min_profit_raw"]))
        for i, step in enumerate(p["steps"]):
            self.assertEqual(env[f"ZERO_STEP{i}_TARGET"].lower(), step["target"].lower())
            self.assertEqual(env[f"ZERO_STEP{i}_DATA"], step["data"])

    def test_rejects_payload_without_three_steps(self):
        p = payload()
        p["steps"] = p["steps"][:2]
        with self.assertRaises(ValueError):
            run_live_candidate_fork("https://arb1.arbitrum.io/rpc", p)

    def test_foundry_contract_reads_exact_environment_steps(self):
        text = Path("contracts/test/ZeroLiveCandidateFork.t.sol").read_text()
        for marker in [
            'envAddress("ZERO_ASSET")',
            'envUint("ZERO_LOAN_RAW")',
            'envUint("ZERO_MIN_PROFIT_RAW")',
            'envBytes("ZERO_STEP0_DATA")',
            'envBytes("ZERO_STEP1_DATA")',
            'envBytes("ZERO_STEP2_DATA")',
            "executor.run",
        ]:
            self.assertIn(marker, text)

    def test_live_candidate_runner_is_exact_block(self):
        text = Path("scripts/live_candidate_fork_test.sh").read_text()
        self.assertIn("ZeroLiveCandidateForkTest", text)
        self.assertIn("--fork-url", text)
        self.assertIn("--fork-block-number", text)
        self.assertIn("FORK_BLOCK", text)


if __name__ == "__main__":
    unittest.main()
