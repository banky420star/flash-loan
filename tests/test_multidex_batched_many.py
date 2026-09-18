import time
import unittest

from zero.venues.base import PoolRef
from zero.venues.camelot_v3 import CamelotV3Adapter
from zero.venues.multidex import discover_route_configs_many
from zero.venues.uniswap_v3 import UniswapV3Adapter


A = "0x" + "11" * 20
B = "0x" + "22" * 20
C = "0x" + "33" * 20
P1 = "0x" + "41" * 20
P2 = "0x" + "42" * 20
P3 = "0x" + "43" * 20
P4 = "0x" + "44" * 20
FACTORY = "0x" + "55" * 20
ROUTER = "0x" + "66" * 20
QUOTER = "0x" + "77" * 20


def address_reply(addr):
    return int(addr, 16).to_bytes(32, "big")


class BatchRpc:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def batch_eth_call_results(self, calls, *, block="latest", max_batch=100):
        self.calls.append((list(calls), block, max_batch))
        return list(self.replies)


class Token:
    def __init__(self, symbol, address, decimals=18, price_usd=1.0):
        self.symbol = symbol
        self.address = address
        self.decimals = decimals
        self.price_usd = price_usd


class TestBatchedVenueDiscovery(unittest.TestCase):
    def test_uniswap_discovers_multiple_pairs_in_one_batched_call(self):
        rpc = BatchRpc([address_reply(P1), address_reply(P2),
                        address_reply(P3), address_reply(P4)])
        adapter = UniswapV3Adapter(
            "uniswap_v3", rpc, FACTORY, ROUTER, QUOTER,
            (500, 3000), True, True, "uniswap_v3")

        found = adapter.discover_pairs([(A, B), (A, C)], 777, max_batch=17)

        self.assertEqual(len(rpc.calls), 1)
        self.assertEqual(len(rpc.calls[0][0]), 4)
        self.assertEqual(rpc.calls[0][1], 777)
        self.assertEqual(rpc.calls[0][2], 17)
        self.assertEqual([p.address for p in found[(A, B)]], [P1, P2])
        self.assertEqual([p.address for p in found[(A, C)]], [P3, P4])

    def test_camelot_discovers_multiple_pairs_in_one_batched_call(self):
        rpc = BatchRpc([address_reply(P1), address_reply(P2)])
        adapter = CamelotV3Adapter(
            "camelot_v3", rpc, FACTORY, ROUTER, QUOTER,
            (), True, False, "algebra_v3")

        found = adapter.discover_pairs([(A, B), (A, C)], 888, max_batch=9)

        self.assertEqual(len(rpc.calls), 1)
        self.assertEqual(len(rpc.calls[0][0]), 2)
        self.assertEqual(rpc.calls[0][1], 888)
        self.assertEqual(rpc.calls[0][2], 9)
        self.assertEqual(found[(A, B)][0].address, P1)
        self.assertEqual(found[(A, C)][0].address, P2)


class StaticAdapter:
    exact_quote_supported = True

    def __init__(self, venue_id, pools, delay=0.0):
        self.venue_id = venue_id
        self.pools = pools
        self.delay = delay

    def discover_pairs(self, pairs, block, *, max_batch=100):
        if self.delay:
            time.sleep(self.delay)
        return {pair: list(self.pools) for pair in pairs}


class TestConcurrentVenueDiscovery(unittest.TestCase):
    def test_slow_venue_times_out_without_blocking_fast_venue(self):
        fast_pools = [
            PoolRef("sushi_v3", P1, A, B, 500, "uniswap_v3"),
            PoolRef("sushi_v3", P2, A, B, 3000, "uniswap_v3"),
        ]
        venues = {
            "sushi_v3": StaticAdapter("sushi_v3", fast_pools),
            "camelot_v3": StaticAdapter("camelot_v3", [], delay=0.25),
        }
        tokens = {"A": Token("A", A), "B": Token("B", B)}

        started = time.monotonic()
        rows, errors = discover_route_configs_many(
            venues, [("A", "B")], tokens, 42161, 999,
            max_batch=20, max_workers=2, timeout_s=0.04)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)
        self.assertTrue(rows)
        self.assertEqual(errors[0]["venue_id"], "camelot_v3")
        self.assertEqual(errors[0]["error"], "timeout")


if __name__ == "__main__":
    unittest.main()

class TestRotatingRouteBudget(unittest.TestCase):
    def test_per_pair_cap_rotates_across_blocks(self):
        uni_pools = [
            PoolRef("uniswap_v3", P1, A, B, 500, "uniswap_v3"),
            PoolRef("uniswap_v3", P2, A, B, 3000, "uniswap_v3"),
        ]
        sushi_pools = [
            PoolRef("sushi_v3", P3, A, B, 500, "uniswap_v3"),
            PoolRef("sushi_v3", P4, A, B, 3000, "uniswap_v3"),
        ]
        venues = {
            "uniswap_v3": StaticAdapter("uniswap_v3", uni_pools),
            "sushi_v3": StaticAdapter("sushi_v3", sushi_pools),
        }
        tokens = {"A": Token("A", A), "B": Token("B", B)}
        first, errors1 = discover_route_configs_many(
            venues, [("A", "B")], tokens, 42161, 100,
            max_batch=20, max_workers=2, timeout_s=1.0,
            max_routes_per_pair=1)
        second, errors2 = discover_route_configs_many(
            venues, [("A", "B")], tokens, 42161, 101,
            max_batch=20, max_workers=2, timeout_s=1.0,
            max_routes_per_pair=1)
        self.assertFalse(errors1)
        self.assertFalse(errors2)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0]["route_id"], second[0]["route_id"])

class TestSeededUniswapPools(unittest.TestCase):
    def test_seeded_uniswap_pool_combines_with_discovered_sushi_pool(self):
        seed = PoolRef("uniswap_v3", P1, A, B, 500, "uniswap_v3")
        sushi = PoolRef("sushi_v3", P2, A, B, 500, "uniswap_v3")
        venues = {"sushi_v3": StaticAdapter("sushi_v3", [sushi])}
        tokens = {"A": Token("A", A), "B": Token("B", B)}
        rows, errors = discover_route_configs_many(
            venues, [("A", "B")], tokens, 42161, 777,
            max_batch=20, max_workers=1, timeout_s=1.0,
            max_routes_per_pair=4,
            seed_pools_by_pair={(A.lower(), B.lower()): [seed]})
        self.assertFalse(errors)
        self.assertTrue(rows)
        self.assertTrue(any(
            row["leg1"]["venue_id"] != row["leg2"]["venue_id"]
            for row in rows))
