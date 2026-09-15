import time
import unittest

from zero.gate import Gate
from zero.ledger import Ledger
from zero.strategies.liquidation import bucket_for, evaluate_account


class TestGate(unittest.TestCase):
    def test_floor_applies(self):
        g = Gate(floor_usd=2.0)
        d, _, min_p = g.evaluate(net_profit=1.5, gross_profit=1.5,
                                 gas_cost_usd=0.1, loan_size_usd=100)
        self.assertEqual(d, "REJECT")
        self.assertEqual(min_p, 2.0)

    def test_gas_multiple_beats_floor(self):
        g = Gate(floor_usd=2.0, gas_multiple=4.0)
        d, _, min_p = g.evaluate(net_profit=10.0, gross_profit=11.0,
                                 gas_cost_usd=5.0, loan_size_usd=100)
        self.assertEqual(d, "REJECT")  # min = max(2, 20, 0.01) = 20
        self.assertEqual(min_p, 20.0)

    def test_pass(self):
        g = Gate(floor_usd=2.0, gas_multiple=4.0, min_roi=0.0001)
        d, reason, _ = g.evaluate(net_profit=25.0, gross_profit=26.0,
                                  gas_cost_usd=5.0, loan_size_usd=5000)
        self.assertEqual(d, "PASS")

    def test_stale_rejected(self):
        g = Gate(max_age_ms=500)
        now = time.time() * 1000
        d, reason, _ = g.evaluate(10.0, 11.0, 0.1, 100.0,
                                  discovered_at_ms=now - 2000, now_ms=now)
        self.assertEqual(d, "REJECT")
        self.assertIn("stale", reason)

    def test_negative_gross_rejected(self):
        g = Gate()
        d, reason, _ = g.evaluate(-1.0, -1.0, 0.1, 100.0)
        self.assertEqual(d, "REJECT")


class TestLiquidation(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(bucket_for(0.998), "LIQUIDATABLE")
        self.assertEqual(bucket_for(1.002), "CRITICAL")
        self.assertEqual(bucket_for(1.01), "HOT")
        self.assertEqual(bucket_for(1.03), "WARM")
        self.assertEqual(bucket_for(1.07), "WATCH")
        self.assertEqual(bucket_for(2.5), "SAFE")

    def test_liquidatable_record(self):
        raw = {"collateral_usd": 50000.0, "debt_usd": 49000.0,
               "liquidation_threshold": 0.8, "health_factor": 0.997}
        rec = evaluate_account(raw, bonus=0.05)
        self.assertTrue(rec["liquidatable"])
        self.assertAlmostEqual(rec["max_repay_usd"], 24500.0)
        self.assertAlmostEqual(rec["est_gross_profit_usd"], 1225.0)

    def test_safe_account(self):
        raw = {"collateral_usd": 50000.0, "debt_usd": 10000.0,
               "liquidation_threshold": 0.8, "health_factor": 3.99}
        rec = evaluate_account(raw)
        self.assertFalse(rec["liquidatable"])


class TestLedger(unittest.TestCase):
    def test_round_trip(self):
        import tempfile, os
        path = os.path.join(tempfile.mkdtemp(), "t.db")
        led = Ledger(path)
        led.record(block=100, strategy="arbitrage", decision="PASS",
                   asset="USDC", loan_size=47000.0, gross=25.0, net=20.0,
                   min_profit=8.0, reason="ok")
        led.record(block=101, strategy="liquidation", decision="REJECT",
                   reason="stale: age 900ms > 500ms")
        rows = led.tail(10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["strategy"], "liquidation")
        stats = led.stats()
        self.assertEqual(stats["arbitrage"]["PASS"], 1)
        self.assertEqual(stats["liquidation"]["REJECT"], 1)
        led.record_cycle(block=101, detected=2, passed=1, rejected=1)
        led.close()
        # reopen persists
        led2 = Ledger(path)
        self.assertEqual(len(led2.tail(10)), 2)
        led2.close()


if __name__ == "__main__":
    unittest.main()