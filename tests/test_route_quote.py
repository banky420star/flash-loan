import unittest
from decimal import Decimal

from zero.route_quote import (
    RouteQuoteError,
    evaluate_route_economics,
    quote_route,
)
from zero.routes import RouteCandidate, RouteLeg
from zero.venues.base import PoolRef, VenueQuote


A = "0x" + "11" * 20
B = "0x" + "22" * 20
C = "0x" + "33" * 20
P1 = "0x" + "a1" * 20
P2 = "0x" + "a2" * 20
P3 = "0x" + "a3" * 20


def pool(venue, address, a, b, fee=500):
    first, second = sorted((a, b), key=lambda x: int(x, 16))
    return PoolRef(venue, address, first, second, fee, "uniswap_v3")


class Adapter:
    exact_quote_supported = True

    def __init__(self, factor, gas):
        self.factor = factor
        self.gas = gas
        self.calls = []

    def quote_exact_input(self, pool_ref, token_in, amount_in, block):
        self.calls.append((pool_ref.id, token_in, amount_in, block))
        return VenueQuote(amount_in * self.factor, self.gas, pool_ref.fee_tier)


class TestRouteQuote(unittest.TestCase):
    def test_each_leg_receives_previous_exact_output(self):
        p1 = pool("uniswap_v3", P1, A, B)
        p2 = pool("sushi_v3", P2, B, A)
        route = RouteCandidate(42161, A, (
            RouteLeg(p1, A, B), RouteLeg(p2, B, A)))
        first = Adapter(2, 40_000)
        second = Adapter(3, 50_000)
        result = quote_route(route, 100, 777, {
            "uniswap_v3": first, "sushi_v3": second})
        self.assertEqual(first.calls[0][2:], (100, 777))
        self.assertEqual(second.calls[0][2:], (200, 777))
        self.assertEqual(result.amount_out_raw, 600)
        self.assertEqual(result.gas_estimate, 90_000)
        self.assertEqual(len(result.leg_quotes), 2)

    def test_three_leg_sequence_is_supported(self):
        pools = [
            pool("uniswap_v3", P1, A, B),
            pool("sushi_v3", P2, B, C),
            pool("camelot_v3", P3, C, A, None),
        ]
        route = RouteCandidate(42161, A, (
            RouteLeg(pools[0], A, B), RouteLeg(pools[1], B, C),
            RouteLeg(pools[2], C, A)))
        adapters = {name: Adapter(2, 10) for name in (
            "uniswap_v3", "sushi_v3", "camelot_v3")}
        result = quote_route(route, 10, 888, adapters)
        self.assertEqual(result.amount_out_raw, 80)
        self.assertEqual(result.gas_estimate, 30)

    def test_unsupported_leg_rejects_route(self):
        p1 = pool("uniswap_v3", P1, A, B)
        p2 = pool("sushi_v3", P2, B, A)
        route = RouteCandidate(42161, A, (
            RouteLeg(p1, A, B), RouteLeg(p2, B, A)))
        good = Adapter(2, 1)
        bad = Adapter(1, 1)
        bad.exact_quote_supported = False
        with self.assertRaises(RouteQuoteError):
            quote_route(route, 100, 777, {
                "uniswap_v3": good, "sushi_v3": bad})

    def test_economics_use_decimal_at_one_raw_unit_boundary(self):
        p1 = pool("uniswap_v3", P1, A, B)
        p2 = pool("uniswap_v3", P2, B, A)
        route = RouteCandidate(42161, A, (
            RouteLeg(p1, A, B), RouteLeg(p2, B, A)))
        first = Adapter(1, 0)
        second = Adapter(1, 0)
        quote = quote_route(route, 100_000_000, 777,
                            {"uniswap_v3": first})
        object.__setattr__(quote, "amount_out_raw", 100_200_001)
        economics = evaluate_route_economics(
            quote, base_decimals=6, base_price_usd=Decimal("1"),
            premium_bps=5, gas_usd=Decimal("0.10"),
            reserve_usd=Decimal("0.05"))
        self.assertEqual(economics.expected_net_usd, Decimal("0.000001"))
        self.assertTrue(economics.positive)
        object.__setattr__(quote, "amount_out_raw", 100_200_000)
        zero = evaluate_route_economics(
            quote, base_decimals=6, base_price_usd=Decimal("1"),
            premium_bps=5, gas_usd=Decimal("0.10"),
            reserve_usd=Decimal("0.05"))
        self.assertEqual(zero.expected_net_usd, Decimal("0.000000"))
        self.assertFalse(zero.positive)


if __name__ == "__main__":
    unittest.main()
