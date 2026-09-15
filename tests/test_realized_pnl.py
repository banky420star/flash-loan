import json
import os
import tempfile
import unittest
from unittest.mock import patch

from zero.fork import ForkResult
from zero.fork_cli import run_live_candidate_fork_result


CANDIDATE = {
    "block": 123,
    "base_asset": "0x" + "11" * 20,
    "base_decimals": 6,
    "loan_amount_raw": 100_000_000,
    "min_profit_raw": 50_000,
    "predicted_net": 0.08,
    "gas_cost_usd": 0.20,
}

STEPS = [
    {"target": "0x" + f"{i + 2:02x}" * 20, "value": 0, "data": "0x1234"}
    for i in range(3)
]

PAYLOAD = {
    "candidate": CANDIDATE,
    "steps": STEPS,
    "verification": {
        "candidate_id": "candidate-1",
        "route_id": "route-1",
        "base_price_usd": 1.0,
        "model_reserve_usd": 0.02,
        "gas_limit": 2_000_000,
    },
}


class FakeCompleted:
    def __init__(self, returncode=0, stdout="forge ok", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestStructuredForkResult(unittest.TestCase):
    def _fake_run(self, realized_raw=150_000, gas_used=1_000_000, returncode=0):
        def run(args, env=None, capture_output=None, text=None):
            self.assertTrue(capture_output)
            self.assertTrue(text)
            result_path = env["ZERO_RESULT_PATH"]
            os.makedirs(os.path.dirname(result_path), exist_ok=True)
            if returncode == 0:
                with open(result_path, "w") as f:
                    json.dump({
                        "realized_raw": str(realized_raw),
                        "gas_used": str(gas_used),
                    }, f)
            return FakeCompleted(returncode=returncode)
        return run

    def test_success_returns_realized_net_and_model_error(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("zero.fork_cli.RESULT_DIR", tmp), \
             patch("zero.fork_cli.subprocess.run", side_effect=self._fake_run()):
            result = run_live_candidate_fork_result("https://upstream", PAYLOAD)

        self.assertIsInstance(result, ForkResult)
        self.assertTrue(result.success)
        self.assertEqual(result.block, 123)
        self.assertEqual(result.strategy, "swarm_arbitrage")
        self.assertEqual(result.gas_used, 1_000_000)
        # 0.150 USDC realized after Aave repayment; measured fork gas is half
        # the configured 2M budget, so scaled modeled gas is $0.10.
        self.assertAlmostEqual(result.realized_net, 0.05)
        # predicted model net excludes the admission reserve: 0.08 + 0.02.
        self.assertAlmostEqual(result.predicted_net, 0.10)
        self.assertAlmostEqual(result.model_error, -0.05)
        detail = json.loads(result.detail)
        self.assertAlmostEqual(detail["realized_profit_usd"], 0.15)
        self.assertAlmostEqual(detail["estimated_gas_cost_usd"], 0.10)
        self.assertEqual(detail["candidate_id"], "candidate-1")
        self.assertEqual(detail["route_id"], "route-1")

    def test_failed_fork_returns_structured_failure_without_result_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("zero.fork_cli.RESULT_DIR", tmp), \
             patch("zero.fork_cli.subprocess.run", side_effect=self._fake_run(returncode=1)):
            result = run_live_candidate_fork_result("https://upstream", PAYLOAD)

        self.assertFalse(result.success)
        self.assertEqual(result.gas_used, 0)
        self.assertEqual(result.realized_net, 0.0)
        self.assertAlmostEqual(result.predicted_net, 0.10)
        detail = json.loads(result.detail)
        self.assertEqual(detail["returncode"], 1)
        self.assertEqual(detail["candidate_id"], "candidate-1")

    def test_result_file_is_required_on_success(self):
        def run_without_result(*args, **kwargs):
            return FakeCompleted(returncode=0)

        with tempfile.TemporaryDirectory() as tmp, \
             patch("zero.fork_cli.RESULT_DIR", tmp), \
             patch("zero.fork_cli.subprocess.run", side_effect=run_without_result):
            result = run_live_candidate_fork_result("https://upstream", PAYLOAD)

        self.assertFalse(result.success)
        self.assertIn("missing_result_file", result.detail)


if __name__ == "__main__":
    unittest.main()
