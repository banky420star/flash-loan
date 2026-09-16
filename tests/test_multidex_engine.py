import unittest

from zero.engine import ShadowEngine
from zero.swarm import ScanContext, TokenInfo
from zero.venues.base import PoolRef, VenueQuote

A = "0x" + "11" * 20
B = "0x" + "22" * 20
P1 = "0x" + "33" * 20
P2 = "0x" + "44" * 20


class QuoteAdapter:
    def __init__(self, amount_out, *, execution_supported=False):
        self.amount_out = amount_out
        self.exact_quote_supported = True
        self.execution_supported = execution_supported
        self.calls = []

    def quote_exact_input(self, pool, token_in, amount_in, block):
        self.calls.append((pool.address, token_in, amount_in, block))
        return VenueQuote(self.amount_out(amount_in), gas_estimate=50_000,
                          fee_used=500)


class TestMultiDexEngine(unittest.TestCase):
    def make_engine(self, second_execution=False):
        engine = object.__new__(ShadowEngine)
        engine.config = {
            "swarm": {"size_ladder_usd": [100]},
            "gas_limit": 2_000_000,
        }
        first = QuoteAdapter(lambda amount: amount * 2,
                             execution_supported=True)
        second = QuoteAdapter(lambda amount: amount * 51 // 100,
                              execution_supported=second_execution)
        engine.venues = {"uniswap_v3": first, "sushi_v3": second}
        return engine, first, second

    def make_context(self):
        return ScanContext(
            block=777, aave_pool="0x" + "aa" * 20,
            oracle="0x" + "bb" * 20, premium_bps=5,
            eth_price_usd=2000.0, gas_usd=0.10,
            tokens={"USDC": TokenInfo("USDC", A, 6, 1.0)},
            pool_states={},
        )

    def route(self):
        return {
            "block": 777, "route_kind": "multidex_exact",
            "route_id": "route-1", "name": "uni -> sushi",
            "base_symbol": "USDC", "quote_symbol": "WETH",
            "base": A, "quote": B,
            "base_decimals": 6, "quote_decimals": 6,
            "base_price_usd": 1.0,
            "leg1": PoolRef("uniswap_v3", P1, A, B, 500,
                            "uniswap_v3").__dict__,
            "leg2": PoolRef("sushi_v3", P2, A, B, 500,
                            "uniswap_v3").__dict__,
        }

    def test_positive_cross_venue_route_is_observation_only_without_encoder(self):
        engine, first, second = self.make_engine(second_execution=False)
        row = engine.scan_multidex_route(
            777, self.route(), model_reserve_usd=0.05,
            context=self.make_context())
        self.assertGreater(row["expected_net_usd"], 0)
        self.assertEqual(row["decision"], "OBSERVE")
        self.assertIsNone(row["candidate"])
        self.assertEqual(first.calls[0][3], 777)
        self.assertEqual(second.calls[0][3], 777)
        self.assertAlmostEqual(row["expected_net_usd"], 1.80, places=6)


if __name__ == "__main__":
    unittest.main()


class TestMultiDexProbe(unittest.TestCase):
    def test_negative_probe_does_not_scan_full_size_ladder(self):
        engine = object.__new__(ShadowEngine)
        engine.config = {"chain_id": 42161, "swarm": {
            "size_ladder_usd": [100, 250, 500],
            "multidex_probe_usd": 100,
        }}
        first = QuoteAdapter(lambda amount: amount * 2,
                             execution_supported=True)
        second = QuoteAdapter(lambda amount: amount * 49 // 100,
                              execution_supported=False)
        engine.venues = {"uniswap_v3": first, "sushi_v3": second}
        base = TokenInfo("USDC", A, 6, 1.0)
        context = ScanContext(
            block=777, aave_pool="pool", oracle="oracle", premium_bps=5,
            eth_price_usd=2000.0, gas_usd=0.10,
            tokens={"USDC": base}, pool_states={})
        route = TestMultiDexEngine().route()
        row = engine.scan_multidex_route(
            777, route, model_reserve_usd=0.05, context=context)
        self.assertEqual(row["decision"], "REJECT")
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)


class RaisingAdapter:
    exact_quote_supported = True
    execution_supported = False

    def __init__(self, error):
        self.error = error

    def quote_exact_input(self, pool, token_in, amount_in, block):
        raise self.error


class TestMultiDexQuoteFailures(unittest.TestCase):
    def build_engine(self, error):
        from zero.rpc import RpcError
        engine = object.__new__(ShadowEngine)
        engine.config = {"chain_id": 42161, "swarm": {"size_ladder_usd": [100]}}
        engine.venues = {"uniswap_v3": RaisingAdapter(error),
                         "sushi_v3": QuoteAdapter(lambda amount: amount)}
        return engine

    def context(self):
        return ScanContext(777, "pool", "oracle", 5, 2000.0, 0.1,
                           {"USDC": TokenInfo("USDC", A, 6, 1.0)}, {})
    def test_contract_revert_is_treated_as_unquotable_route(self):
        from zero.rpc import RpcError
        engine = self.build_engine(RpcError("RPC error: {'code': 3, 'message': 'execution reverted: SPL'}"))
        row = engine.scan_multidex_route(
            777, TestMultiDexEngine().route(), context=self.context())
        self.assertIsNone(row)

    def test_transport_failure_still_propagates(self):
        from zero.rpc import RpcError
        engine = self.build_engine(RpcError(
            "rpc unreachable after 3 attempts: HTTP Error 429: Too Many Requests"))
        with self.assertRaises(RpcError):
            engine.scan_multidex_route(
                777, TestMultiDexEngine().route(), context=self.context())
