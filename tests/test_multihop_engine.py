import unittest

from zero.engine import ShadowEngine
from zero.swarm import ScanContext, TokenInfo
from zero.venues.base import PoolRef, VenueQuote


A = "0x" + "11" * 20
B = "0x" + "22" * 20
C = "0x" + "33" * 20
P1 = "0x" + "a1" * 20
P2 = "0x" + "a2" * 20
P3 = "0x" + "a3" * 20


class Adapter:
    exact_quote_supported = True
    execution_supported = False

    def __init__(self, numerator, denominator=1):
        self.numerator = numerator
        self.denominator = denominator
        self.calls = []

    def quote_exact_input(self, pool, token_in, amount_in, block):
        self.calls.append((pool.address, token_in, amount_in, block))
        return VenueQuote(amount_in * self.numerator // self.denominator,
                          gas_estimate=10_000, fee_used=500)


def pool(venue, address, x, y):
    first, second = sorted((x, y), key=lambda value: int(value, 16))
    return PoolRef(venue, address, first, second, 500, "uniswap_v3")


class TestMultiHopEngine(unittest.TestCase):
    def route(self):
        p1 = pool("uniswap_v3", P1, A, B)
        p2 = pool("sushi_v3", P2, B, C)
        p3 = pool("camelot_v3", P3, C, A)
        return {
            "block": 777, "route_kind": "multihop_exact",
            "route_id": "three-leg", "base_symbol": "USDC",
            "quote_symbol": "WETH", "base": A, "quote": B,
            "base_decimals": 6, "quote_decimals": 6,
            "base_price_usd": 1.0, "executable": False,
            "legs": [
                {"pool": p1.__dict__.copy(), "token_in": A, "token_out": B},
                {"pool": p2.__dict__.copy(), "token_in": B, "token_out": C},
                {"pool": p3.__dict__.copy(), "token_in": C, "token_out": A},
            ],
        }

    def test_profitable_three_leg_quote_returns_non_executable_candidate(self):
        engine = object.__new__(ShadowEngine)
        engine.config = {"chain_id": 42161, "swarm": {
            "size_ladder_usd": [100], "multidex_probe_usd": 100}}
        engine.venues = {
            "uniswap_v3": Adapter(2),
            "sushi_v3": Adapter(2),
            "camelot_v3": Adapter(26, 100),
        }
        context = ScanContext(
            777, "pool", "oracle", 5, 2000.0, 0.10,
            {"USDC": TokenInfo("USDC", A, 6, 1.0)}, {})
        row = engine.scan_multihop_route(
            777, self.route(), model_reserve_usd=0.05, context=context)

        self.assertEqual(row["decision"], "OBSERVE")
        self.assertAlmostEqual(row["expected_net_usd"], 3.80, places=6)
        self.assertIsNotNone(row["candidate"])
        self.assertFalse(row["candidate"]["executable"])
        self.assertEqual(len(row["candidate"]["legs"]), 3)
        self.assertEqual(
            engine.venues["sushi_v3"].calls[0][2], 200_000_000)
        self.assertEqual(
            engine.venues["camelot_v3"].calls[0][2], 400_000_000)


if __name__ == "__main__":
    unittest.main()
