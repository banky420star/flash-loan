import unittest

from zero.venues.camelot_v3 import CamelotV3Adapter
from zero.venues.uniswap_v3 import UniswapV3Adapter
from zero.venues.multidex import build_two_leg_routes


A = "0x" + "11" * 20
B = "0x" + "22" * 20
P1 = "0x" + "33" * 20
P2 = "0x" + "44" * 20
F1 = "0x" + "55" * 20
F2 = "0x" + "66" * 20
Q = "0x" + "77" * 20
R = "0x" + "88" * 20


def address_reply(addr):
    return int(addr, 16).to_bytes(32, "big")


class FakeRpc:
    def __init__(self):
        self.batch_calls = []
        self.calls = []

    def batch_eth_call(self, calls, *, block="latest", max_batch=100):
        self.batch_calls.append((list(calls), block, max_batch))
        return [address_reply(P1), address_reply(P2)]

    def eth_call(self, to, data, block="latest"):
        self.calls.append((to, data, block))
        return address_reply(P2)


class TestMultiDexDiscovery(unittest.TestCase):
    def test_v3_discovery_is_batched_and_pinned(self):
        rpc = FakeRpc()
        adapter = UniswapV3Adapter(
            "uniswap_v3", rpc, F1, R, Q, (500, 3000), True, True, "uniswap_v3")
        pools = adapter.discover_pair(A, B, 777)
        self.assertEqual(len(pools), 2)
        self.assertEqual(rpc.batch_calls[0][1], 777)
        self.assertEqual([p.fee_tier for p in pools], [500, 3000])
        self.assertTrue(all(p.venue_id == "uniswap_v3" for p in pools))

    def test_algebra_discovery_uses_pool_by_pair_at_pinned_block(self):
        rpc = FakeRpc()
        adapter = CamelotV3Adapter(
            "camelot_v3", rpc, F2, R, Q, (), True, False, "algebra_v3")
        pools = adapter.discover_pair(A, B, 888)
        self.assertEqual(len(pools), 1)
        self.assertEqual(pools[0].address, P2.lower())
        self.assertEqual(rpc.calls[0][2], 888)
        self.assertIsNone(pools[0].fee_tier)

    def test_cross_venue_routes_are_directional_and_venue_specific(self):
        rpc = FakeRpc()
        uni = UniswapV3Adapter(
            "uniswap_v3", rpc, F1, R, Q, (500, 3000), True, True, "uniswap_v3")
        camelot = CamelotV3Adapter(
            "camelot_v3", rpc, F2, R, Q, (), True, False, "algebra_v3")
        pools = uni.discover_pair(A, B, 777) + camelot.discover_pair(A, B, 777)
        routes = build_two_leg_routes(42161, A, B, pools, block=777)
        cross = [r for r in routes if r.leg1.venue_id != r.leg2.venue_id]
        self.assertTrue(cross)
        self.assertEqual(len({r.id for r in routes}), len(routes))
        self.assertTrue(all(r.leg1.id != r.leg2.id for r in routes))


if __name__ == "__main__":
    unittest.main()


class Token:
    def __init__(self, symbol, address, decimals, price_usd):
        self.symbol = symbol
        self.address = address
        self.decimals = decimals
        self.price_usd = price_usd


class TestMultiDexCatalog(unittest.TestCase):
    def test_discover_route_configs_emits_non_uniswap_exact_routes(self):
        from zero.venues.multidex import discover_route_configs
        rpc = FakeRpc()
        venues = {
            "uniswap_v3": UniswapV3Adapter(
                "uniswap_v3", rpc, F1, R, Q, (500, 3000), True, True,
                "uniswap_v3"),
            "camelot_v3": CamelotV3Adapter(
                "camelot_v3", rpc, F2, R, Q, (), True, False,
                "algebra_v3"),
        }
        tokens = {"USDC": Token("USDC", A, 6, 1.0),
                  "WETH": Token("WETH", B, 18, 2000.0)}
        rows = discover_route_configs(
            venues, ("USDC", "WETH"), tokens, 42161, 777)
        self.assertTrue(rows)
        self.assertTrue(all(r["route_kind"] == "multidex_exact" for r in rows))
        self.assertTrue(all(not (
            r["leg1"]["venue_id"] == "uniswap_v3" and
            r["leg2"]["venue_id"] == "uniswap_v3") for r in rows))
        self.assertEqual(len({r["route_id"] for r in rows}), len(rows))
