import math
import unittest

from zero.strategies.arbitrage import Cycle, best_opportunity, sweep_sizes
from zero.uniswap_v3 import UniswapV3Pool
from tests.test_uniswap import DEEP_LIQUIDITY, state_from_price

# ~$3.5M pools: impact curves make an interior optimum inside the sweep
MEDIUM_LIQUIDITY = 5.9e16

WETH, USDC = "0xweth", "0xusdc"


def pool(fee=0.05):
    return UniswapV3Pool(None, "0xpool", WETH, USDC, fee, 18, 6)


class TestArbitrage(unittest.TestCase):
    def _cycle(self, fee_a=0.05, fee_b=0.3):
        return Cycle(pool(fee_a), pool(fee_b), USDC, 6, 18)

    def test_spread_yields_positive_gross(self):
        cyc = self._cycle()
        # A sells WETH at 3500, B buys at 3540 -> 114bps spread vs 35bps fees
        s_a = state_from_price(3500.0, MEDIUM_LIQUIDITY)
        s_b = state_from_price(3540.0, MEDIUM_LIQUIDITY)
        curve = cyc.profit_curve(s_a, s_b, sweep_sizes(10, 50000))
        best = best_opportunity(curve)
        self.assertIsNotNone(best)
        self.assertGreater(best["gross"], 0)
        # optimal size is finite, not the max of the sweep
        self.assertLess(best["size"], 50000 * 0.99)

    def test_no_spread_no_opportunity(self):
        cyc = self._cycle()
        s = state_from_price(3500.0, DEEP_LIQUIDITY)
        curve = cyc.profit_curve(s, dict(s), sweep_sizes(10, 50000))
        self.assertIsNone(best_opportunity(curve))

    def test_directions_resolve_correctly(self):
        # base = USDC = token1 on both pools: hop A is 1to0, hop B is 0to1
        cyc = self._cycle()
        self.assertEqual(cyc._dir_a, "1to0")
        self.assertEqual(cyc._dir_b, "0to1")

    def test_pools_must_share_pair(self):
        other = UniswapV3Pool(None, "0xp", "0xtoken3", USDC, 0.3, 18, 6)
        with self.assertRaises(ValueError):
            Cycle(pool(), other, USDC, 6, 18)


if __name__ == "__main__":
    unittest.main()