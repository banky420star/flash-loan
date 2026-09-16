import unittest

from zero.fork import ForkResult


class TestForkOutcomeClass(unittest.TestCase):
    def make_result(self, **overrides):
        values = {
            "block": 123,
            "strategy": "swarm_arbitrage",
            "success": True,
            "gas_used": 100,
            "predicted_net": 1.0,
            "realized_net": 0.8,
        }
        values.update(overrides)
        return ForkResult(**values)

    def test_success_defaults_to_measured_success_and_is_reserve_eligible(self):
        result = self.make_result()
        self.assertEqual(result.outcome_class, "measured_success")
        self.assertTrue(result.reserve_eligible)

    def test_execution_revert_is_reserve_eligible(self):
        result = self.make_result(success=False, gas_used=0,
                                  realized_net=0.0,
                                  outcome_class="execution_revert")
        self.assertTrue(result.reserve_eligible)

    def test_infrastructure_and_invalid_harness_are_not_reserve_eligible(self):
        for outcome in ("infrastructure_error", "invalid_harness"):
            result = self.make_result(success=False, gas_used=0,
                                      realized_net=0.0,
                                      outcome_class=outcome)
            self.assertFalse(result.reserve_eligible)

    def test_unknown_outcome_class_is_rejected(self):
        with self.assertRaises(ValueError):
            self.make_result(outcome_class="mystery")

    def test_success_must_match_measured_success_class(self):
        with self.assertRaises(ValueError):
            self.make_result(success=True, outcome_class="execution_revert")
        with self.assertRaises(ValueError):
            self.make_result(success=False, outcome_class="measured_success")

    def test_outcome_class_is_serialized(self):
        result = self.make_result()
        self.assertEqual(result.as_dict()["outcome_class"], "measured_success")


if __name__ == "__main__":
    unittest.main()
