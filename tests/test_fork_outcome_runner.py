import json
import os
import tempfile
import unittest
from unittest.mock import patch

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
PAYLOAD = {"candidate": CANDIDATE, "steps": STEPS,
           "verification": {"model_reserve_usd": 0.02,
                            "base_price_usd": 1.0,
                            "gas_limit": 2_000_000}}


class Completed:
    def __init__(self, code, stdout="", stderr=""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


class TestForkOutcomeRunner(unittest.TestCase):
    def run_case(self, completed, *, write_result=None):
        def fake_run(args, env=None, capture_output=None, text=None):
            if write_result is not None:
                os.makedirs(os.path.dirname(env["ZERO_RESULT_PATH"]), exist_ok=True)
                with open(env["ZERO_RESULT_PATH"], "w") as f:
                    json.dump(write_result, f)
            return completed
        with tempfile.TemporaryDirectory() as tmp, \
             patch("zero.fork_cli.RESULT_DIR", tmp), \
             patch("zero.fork_cli.subprocess.run", side_effect=fake_run):
            return run_live_candidate_fork_result("https://upstream", PAYLOAD)

    def test_strategy_revert_is_execution_evidence(self):
        result = self.run_case(Completed(
            1, stdout="[FAIL: StepFailed(2, 0x1234)] testExactPythonCandidateCalldataOnDetectionBlock()"))
        self.assertEqual(result.outcome_class, "execution_revert")
        self.assertTrue(result.reserve_eligible)

    def test_minimum_profit_revert_is_execution_evidence(self):
        result = self.run_case(Completed(
            1, stdout="[FAIL: MinimumProfitNotMet(1, 2)] testExactPythonCandidateCalldataOnDetectionBlock()"))
        self.assertEqual(result.outcome_class, "execution_revert")

    def test_missing_forge_is_infrastructure_error(self):
        result = self.run_case(Completed(
            2, stderr="ERROR: forge is required. Install Foundry"))
        self.assertEqual(result.outcome_class, "infrastructure_error")
        self.assertFalse(result.reserve_eligible)

    def test_rpc_failure_is_infrastructure_error(self):
        result = self.run_case(Completed(
            1, stderr="error sending request for url: HTTP 429 Too Many Requests"))
        self.assertEqual(result.outcome_class, "infrastructure_error")

    def test_missing_result_after_zero_exit_is_invalid_harness(self):
        result = self.run_case(Completed(0, stdout="forge ok"))
        self.assertEqual(result.outcome_class, "invalid_harness")
        self.assertFalse(result.reserve_eligible)

    def test_successful_result_is_measured_success(self):
        result = self.run_case(
            Completed(0, stdout="forge ok"),
            write_result={"realized_raw": "150000", "gas_used": "1000000"},
        )
        self.assertTrue(result.success)
        self.assertEqual(result.outcome_class, "measured_success")
        self.assertTrue(result.reserve_eligible)


if __name__ == "__main__":
    unittest.main()
