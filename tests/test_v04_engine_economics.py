import unittest

from zero.engine import ShadowEngine


class TestV04EngineEconomics(unittest.TestCase):
    def test_flash_economics_subtracts_premium_and_gas(self):
        result = ShadowEngine.flash_economics(
            gross=25.0,
            loan_size=10_000.0,
            premium_bps=5,
            gas_usd=4.0,
        )
        self.assertAlmostEqual(result["flash_fee"], 5.0)
        self.assertAlmostEqual(result["net"], 16.0)


if __name__ == "__main__":
    unittest.main()
