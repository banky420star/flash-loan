import unittest

from zero.candidate import ArbitrageCandidate


USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"


class TestArbitrageCandidate(unittest.TestCase):
    def test_raw_amounts_and_flash_fee_are_deterministic(self):
        c = ArbitrageCandidate(
            block=123,
            name="USDC round trip",
            base_asset=USDC,
            quote_asset=WETH,
            base_decimals=6,
            quote_decimals=18,
            loan_size=1000.0,
            hop1_expected_out=0.285714285714,
            hop2_expected_out=1004.0,
            fee1=500,
            fee2=3000,
            flash_premium_bps=5,
            gas_cost_usd=1.0,
            gross_profit=4.0,
            predicted_net=2.5,
            min_profit=2.1234561,
        )
        self.assertEqual(c.loan_amount_raw, 1_000_000_000)
        self.assertEqual(c.hop1_expected_out_raw, 285_714_285_714_000_000)
        self.assertEqual(c.hop2_expected_out_raw, 1_004_000_000)
        self.assertEqual(c.flash_fee_raw, 500_000)
        self.assertEqual(c.min_profit_raw, 2_123_457)
        self.assertAlmostEqual(c.flash_fee, 0.5)

    def test_candidate_serializes_all_execution_fields(self):
        c = ArbitrageCandidate(
            block=1, name="x", base_asset=USDC, quote_asset=WETH,
            base_decimals=6, quote_decimals=18, loan_size=10.0,
            hop1_expected_out=0.003, hop2_expected_out=10.1,
            fee1=500, fee2=3000, flash_premium_bps=5,
            gas_cost_usd=0.01, gross_profit=0.1, predicted_net=0.085,
            min_profit=0.01,
        )
        d = c.as_dict()
        self.assertEqual(d["block"], 1)
        self.assertEqual(d["loan_amount_raw"], c.loan_amount_raw)
        self.assertEqual(d["flash_fee_raw"], c.flash_fee_raw)
        self.assertEqual(d["min_profit_raw"], c.min_profit_raw)


if __name__ == "__main__":
    unittest.main()
