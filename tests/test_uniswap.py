import math
import unittest

from zero.uniswap_v3 import UniswapV3Pool

WETH, USDC = "0xweth", "0xusdc"


def state_from_price(p_human: float, liquidity: float,
                     decimals0: int = 18, decimals1: int = 6) -> dict:
    """slot0 state for a pool with marginal price p_human (token1 per token0).

    sqrtPriceX96 encodes the RAW price: p_raw = p * 10^(dec1 - dec0).
    """
    p_raw = p_human * 10 ** (decimals1 - decimals0)
    n = int(math.sqrt(p_raw) * (1 << 96))
    return {"sqrtPriceX96": n, "liquidity": int(liquidity)}


# liquidity for a ~$350M USDC/WETH pool at price 3500 (impact negligible)
DEEP_LIQUIDITY = 5.9e18
# a ~$35k pool, for price-impact tests
SHALLOW_LIQUIDITY = 5.9e14


def pool(fee=0.05):
    return UniswapV3Pool(None, "0xpool", WETH, USDC, fee, 18, 6)


class TestQuote(unittest.TestCase):
    def test_small_swap_matches_spot_price(self):
        state = state_from_price(3500.0, DEEP_LIQUIDITY)
        # USDC in -> WETH out; exact quote includes 2nd-order impact, so a
        # $100 swap into a $3.5M pool sits ~3e-5 below the constant-price value
        r = UniswapV3Pool.quote(state, 100.0, "1to0", 6, 18, 0.05)
        expected = 100.0 * 0.9995 / 3500.0
        self.assertLess(abs(r["out"] - expected) / expected, 1e-4)
        self.assertFalse(r["out_of_range"])

    def test_weth_to_usdc(self):
        state = state_from_price(3500.0, DEEP_LIQUIDITY)
        r = UniswapV3Pool.quote(state, 1.0, "0to1", 18, 6, 0.05)
        expected = 1.0 * 0.9995 * 3500.0
        self.assertLess(abs(r["out"] - expected) / expected, 1e-4)
        self.assertFalse(r["out_of_range"])

    def test_price_impact_monotonic(self):
        state = state_from_price(3500.0, SHALLOW_LIQUIDITY)
        small = UniswapV3Pool.quote(state, 10.0, "1to0", 6, 18, 0.05)["out"]
        big = UniswapV3Pool.quote(state, 100.0, "1to0", 6, 18, 0.05)["out"]
        # effective price worsens with size
        self.assertLess(big / 100.0, small / 10.0)

    def test_large_move_flagged_out_of_range(self):
        state = state_from_price(3500.0, SHALLOW_LIQUIDITY)
        # 1e7 USDC into a $35k pool -> massive move
        r = UniswapV3Pool.quote(state, 1e7, "1to0", 6, 18, 0.05)
        self.assertTrue(r["out_of_range"])

    def test_empty_pool(self):
        r = UniswapV3Pool.quote({"sqrtPriceX96": 0, "liquidity": 0},
                                100.0, "1to0", 6, 18, 0.05)
        self.assertTrue(r["out_of_range"])
        self.assertEqual(r["out"], 0.0)

    def test_spot_price_raw(self):
        state = state_from_price(3500.0, DEEP_LIQUIDITY)
        # raw price = human price * 10^(dec1 - dec0) = 3500e-12
        got = float(UniswapV3Pool.spot_price_raw(state))
        self.assertLess(abs(got - 3500.0e-12) / 3500.0e-12, 1e-9)


if __name__ == "__main__":
    unittest.main()