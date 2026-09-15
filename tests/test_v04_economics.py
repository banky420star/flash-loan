import unittest

from tests.test_arbitrage import MEDIUM_LIQUIDITY, pool
from tests.test_rpc_aave import (
    FakeTransport,
    POOL,
    PROVIDER,
    call_key,
    eth_call_result,
    handlers,
)
from tests.test_uniswap import state_from_price
from zero.aave import AaveV3
from zero.keccak import selector_hex
from zero.rpc import Rpc
from zero.strategies.arbitrage import Cycle


class TestV04Economics(unittest.TestCase):
    def test_flashloan_premium_total_is_read_from_pool(self):
        hs = handlers()
        hs[call_key(POOL, selector_hex("FLASHLOAN_PREMIUM_TOTAL()"))] = eth_call_result([5])
        rpc = Rpc("http://fake", transport=FakeTransport(hs))
        aave = AaveV3(rpc, PROVIDER)
        self.assertEqual(aave.flashloan_premium_total(POOL), 5)

    def test_profit_curve_preserves_both_hop_outputs(self):
        cycle = Cycle(pool(0.05), pool(0.3), "0xusdc", 6, 18)
        state_a = state_from_price(3500.0, MEDIUM_LIQUIDITY)
        state_b = state_from_price(3540.0, MEDIUM_LIQUIDITY)
        row = cycle.profit_curve(state_a, state_b, [1000.0])[0]
        self.assertGreater(row["hop1_out"], 0)
        self.assertGreater(row["hop2_out"], 0)
        self.assertAlmostEqual(row["gross"], row["hop2_out"] - row["size"], places=9)


if __name__ == "__main__":
    unittest.main()
