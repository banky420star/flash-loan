import unittest

from zero.routes import RouteGraph
from zero.venues.base import PoolRef


A = "0x" + "11" * 20
B = "0x" + "22" * 20
C = "0x" + "33" * 20
D = "0x" + "44" * 20
P1 = "0x" + "a1" * 20
P2 = "0x" + "a2" * 20
P3 = "0x" + "a3" * 20
P4 = "0x" + "a4" * 20


def pool(venue, address, a, b, fee=500):
    first, second = sorted((a, b), key=lambda x: int(x, 16))
    return PoolRef(venue, address, first, second, fee, "uniswap_v3")


class TestRouteGraph(unittest.TestCase):
    def test_two_leg_cycle_uses_distinct_pools(self):
        graph = RouteGraph(42161, [
            pool("uniswap_v3", P1, A, B),
            pool("sushi_v3", P2, A, B),
        ])
        cycles = graph.enumerate_cycles(A, max_hops=3)
        self.assertEqual(len(cycles), 2)
        self.assertTrue(all(len(route.legs) == 2 for route in cycles))
        self.assertTrue(all(route.legs[0].pool.id != route.legs[1].pool.id
                            for route in cycles))

    def test_three_leg_cycle_is_closed_and_pool_unique(self):
        graph = RouteGraph(42161, [
            pool("uniswap_v3", P1, A, B),
            pool("sushi_v3", P2, B, C),
            pool("camelot_v3", P3, C, A, None),
        ])
        cycles = graph.enumerate_cycles(A, max_hops=3)
        three = [route for route in cycles if len(route.legs) == 3]
        self.assertEqual(len(three), 2)
        for route in three:
            self.assertEqual(route.legs[0].token_in, A.lower())
            self.assertEqual(route.legs[-1].token_out, A.lower())
            self.assertEqual(len({leg.pool.id for leg in route.legs}), 3)

    def test_open_path_is_not_returned(self):
        graph = RouteGraph(42161, [
            pool("uniswap_v3", P1, A, B),
            pool("sushi_v3", P2, B, C),
            pool("camelot_v3", P3, C, D, None),
        ])
        self.assertEqual(graph.enumerate_cycles(A, max_hops=3), [])

    def test_ordering_and_ids_are_deterministic(self):
        pools = [
            pool("uniswap_v3", P1, A, B),
            pool("sushi_v3", P2, B, C),
            pool("camelot_v3", P3, C, A, None),
            pool("sushi_v3", P4, A, B, 3000),
        ]
        ids1 = [route.id for route in RouteGraph(42161, pools).enumerate_cycles(A, 3)]
        ids2 = [route.id for route in RouteGraph(42161, list(reversed(pools))).enumerate_cycles(A, 3)]
        self.assertEqual(ids1, ids2)
        self.assertEqual(len(ids1), len(set(ids1)))

    def test_max_hops_is_bounded_to_three(self):
        graph = RouteGraph(42161, [])
        with self.assertRaises(ValueError):
            graph.enumerate_cycles(A, max_hops=4)
        with self.assertRaises(ValueError):
            graph.enumerate_cycles(A, max_hops=1)


if __name__ == "__main__":
    unittest.main()
