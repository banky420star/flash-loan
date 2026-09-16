import unittest

from zero.venues.base import PoolRef
from zero.venues.camelot_v3 import CamelotV3Adapter
from zero.venues.multidex import TwoLegRoute
from zero.venues.quotes import quote_leg, route_execution_ready
from zero.venues.uniswap_v3 import UniswapV3Adapter


A = "0x" + "11" * 20
B = "0x" + "22" * 20
P = "0x" + "33" * 20
F = "0x" + "44" * 20
Q = "0x" + "55" * 20
R = "0x" + "66" * 20


def words(*values):
    return b"".join(int(v).to_bytes(32, "big") for v in values)


class FakeRpc:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def eth_call(self, to, data, block="latest"):
        self.calls.append((to, data, block))
        return self.reply


class TestMultiDexQuotes(unittest.TestCase):
    def test_uniswap_compatible_quote_uses_configured_quoter_and_block(self):
        rpc = FakeRpc(words(1234, 99, 2, 45678))
        adapter = UniswapV3Adapter(
            "uniswap_v3", rpc, F, R, Q, (500,), True, True, "uniswap_v3")
        pool = PoolRef("uniswap_v3", P, A, B, 500, "uniswap_v3")
        quote = quote_leg({"uniswap_v3": adapter}, pool, 1000, A, 777)
        self.assertEqual(quote.amount_out, 1234)
        self.assertEqual(quote.gas_estimate, 45678)
        self.assertEqual(rpc.calls[0][0], Q)
        self.assertEqual(rpc.calls[0][2], 777)

    def test_camelot_algebra_quote_returns_dynamic_fee(self):
        rpc = FakeRpc(words(2222, 37))
        adapter = CamelotV3Adapter(
            "camelot_v3", rpc, F, R, Q, (), True, False, "algebra_v3")
        pool = PoolRef("camelot_v3", P, A, B, None, "algebra_v3")
        quote = quote_leg({"camelot_v3": adapter}, pool, 1000, A, 888)
        self.assertEqual(quote.amount_out, 2222)
        self.assertEqual(quote.fee_used, 37)
        self.assertEqual(rpc.calls[0][2], 888)

    def test_route_requires_exact_quotes_and_execution_encoders(self):
        rpc = FakeRpc(words(1, 2, 3, 4))
        uni = UniswapV3Adapter(
            "uniswap_v3", rpc, F, R, Q, (500,), True, True, "uniswap_v3")
        sushi = UniswapV3Adapter(
            "sushi_v3", rpc, F, R, Q, (500,), True, False, "uniswap_v3")
        p1 = PoolRef("uniswap_v3", P, A, B, 500, "uniswap_v3")
        p2 = PoolRef("sushi_v3", "0x" + "77" * 20, A, B, 500, "uniswap_v3")
        route = TwoLegRoute(42161, 777, A, B, p1, p2)
        self.assertFalse(route_execution_ready(route, {"uniswap_v3": uni, "sushi_v3": sushi}))


if __name__ == "__main__":
    unittest.main()
