import unittest

from zero.reserve import AdaptiveReserve


class TestAdaptiveReserve(unittest.TestCase):
    def _policy(self, **overrides):
        cfg = {
            "lookback": 100,
            "min_samples": 3,
            "quantile": 0.90,
            "floor_usd": 0.0,
            "cap_usd": 25.0,
            "bootstrap_reserve_usd": 0.05,
        }
        cfg.update(overrides)
        return AdaptiveReserve(**cfg)

    def test_bootstrap_used_before_minimum_samples(self):
        estimate = self._policy(min_samples=3).estimate([
            {"success": False, "predicted_net": 0.20,
             "realized_net": 0.0, "model_error": -0.20},
            {"success": True, "predicted_net": 0.10,
             "realized_net": 0.08, "model_error": -0.02},
        ])
        self.assertEqual(estimate.samples, 2)
        self.assertAlmostEqual(estimate.value_usd, 0.05)

    def test_adverse_model_error_becomes_positive_reserve_sample(self):
        estimate = self._policy(min_samples=1, quantile=1.0).estimate([
            {"success": True, "predicted_net": 1.0,
             "realized_net": 0.70, "model_error": -0.30},
        ])
        self.assertEqual(estimate.samples, 1)
        self.assertAlmostEqual(estimate.value_usd, 0.30)

    def test_favorable_model_error_contributes_zero_adverse_error(self):
        estimate = self._policy(min_samples=1, quantile=1.0).estimate([
            {"success": True, "predicted_net": 1.0,
             "realized_net": 1.20, "model_error": 0.20},
        ])
        self.assertAlmostEqual(estimate.value_usd, 0.0)

    def test_failed_positive_candidate_counts_as_lost_predicted_edge(self):
        estimate = self._policy(min_samples=1, quantile=1.0).estimate([
            {"success": False, "predicted_net": 0.0493,
             "realized_net": 0.0, "model_error": 0.0},
        ])
        self.assertAlmostEqual(estimate.value_usd, 0.0493)

    def test_nearest_rank_quantile_is_deterministic(self):
        rows = [
            {"success": True, "predicted_net": 1.0,
             "realized_net": 0.99, "model_error": -0.01},
            {"success": True, "predicted_net": 1.0,
             "realized_net": 0.98, "model_error": -0.02},
            {"success": True, "predicted_net": 1.0,
             "realized_net": 0.97, "model_error": -0.03},
            {"success": True, "predicted_net": 1.0,
             "realized_net": 0.96, "model_error": -0.04},
            {"success": True, "predicted_net": 1.0,
             "realized_net": 0.95, "model_error": -0.05},
        ]
        estimate = self._policy(min_samples=1, quantile=0.80).estimate(rows)
        self.assertAlmostEqual(estimate.value_usd, 0.04)

    def test_floor_and_cap_are_enforced(self):
        low = self._policy(min_samples=1, quantile=1.0,
                           floor_usd=0.02).estimate([
            {"success": True, "predicted_net": 1.0,
             "realized_net": 0.995, "model_error": -0.005},
        ])
        high = self._policy(min_samples=1, quantile=1.0,
                            cap_usd=0.25).estimate([
            {"success": False, "predicted_net": 2.0,
             "realized_net": 0.0, "model_error": -2.0},
        ])
        self.assertAlmostEqual(low.value_usd, 0.02)
        self.assertAlmostEqual(high.value_usd, 0.25)

    def test_lookback_limits_samples(self):
        rows = [
            {"success": False, "predicted_net": float(i),
             "realized_net": 0.0, "model_error": -float(i)}
            for i in range(1, 6)
        ]
        estimate = self._policy(lookback=2, min_samples=1,
                                quantile=1.0).estimate(rows)
        self.assertEqual(estimate.samples, 2)
        self.assertAlmostEqual(estimate.value_usd, 2.0)


if __name__ == "__main__":
    unittest.main()
