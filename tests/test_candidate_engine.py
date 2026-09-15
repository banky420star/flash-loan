import unittest

from zero.engine import ShadowEngine


USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"


CYCLE = {
    "name": "USDC->WETH 0.05% | WETH->USDC 0.3%",
    "base": USDC,
    "base_decimals": 6,
    "quote_decimals": 18,
    "pools": [
        {"token0": WETH, "token1": USDC, "fee_tier": 500},
        {"token0": WETH, "token1": USDC, "fee_tier": 3000},
    ],
}


class TestCandidateEngine(unittest.TestCase):
    def test_normalize_candidate_uses_exact_scanner_outputs(self):
        best = {
            "size": 10_000.0,
            "hop1_out": 2.85,
            "hop2_out": 10_020.0,
            "gross": 20.0,
        }
        c = ShadowEngine.normalize_candidate(
            block=777,
            cycle_config=CYCLE,
            best=best,
            premium_bps=5,
            gas_usd=4.0,
            min_profit=8.0,
        )
        self.assertEqual(c.block, 777)
        self.assertEqual(c.base_asset.lower(), USDC.lower())
        self.assertEqual(c.quote_asset.lower(), WETH.lower())
        self.assertEqual(c.fee1, 500)
        self.assertEqual(c.fee2, 3000)
        self.assertEqual(c.hop1_expected_out, 2.85)
        self.assertEqual(c.hop2_expected_out, 10_020.0)
        self.assertAlmostEqual(c.flash_fee, 5.0)
        self.assertAlmostEqual(c.predicted_net, 11.0)
        self.assertAlmostEqual(c.min_profit, 8.0)


if __name__ == "__main__":
    unittest.main()
