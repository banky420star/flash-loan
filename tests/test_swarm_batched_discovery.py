import unittest

from zero.swarm import RouteKey, TokenInfo
from zero.swarm_batch import discover_uniswap_routes_batched


BASE = TokenInfo("BASE", "0x" + "11" * 20, 18, 2500.0)
QUOTE = TokenInfo("QUOTE", "0x" + "22" * 20, 6, 1.0)
FACTORY = "0x" + "33" * 20
POOL_100 = "0x" + "44" * 20
POOL_3000 = "0x" + "55" * 20


def abi_address(address: str) -> bytes:
    return int(address, 16).to_bytes(32, "big")


class FakeRpc:
    def __init__(self):
        self.batches = []

    def batch_eth_call(self, calls, *, block="latest", max_batch=100):
        self.batches.append((list(calls), block, max_batch))
        out = []
        by_fee = {100: POOL_100, 3000: POOL_3000}
        for target, data in calls:
            self.assert_target = target
            fee = int(data[-64:], 16)
            out.append(abi_address(by_fee[fee]) if fee in by_fee else abi_address("0x0"))
        return out


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()
        self.config = {
            "chain_id": 42161,
            "uniswap_v3_factory": FACTORY,
        }


class TestBatchedUniswapDiscovery(unittest.TestCase):
    def test_fee_tier_lookups_share_one_pinned_batch_and_keep_route_identity(self):
        engine = FakeEngine()
        registry = {"BASE": BASE, "QUOTE": QUOTE}
        routes = discover_uniswap_routes_batched(
            engine, ("BASE", "QUOTE"), registry, 321,
            [100, 500, 3000, 10000], max_batch=100)

        self.assertEqual(len(engine.rpc.batches), 1)
        calls, block, max_batch = engine.rpc.batches[0]
        self.assertEqual(block, 321)
        self.assertEqual(max_batch, 100)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(target.lower() == FACTORY.lower() for target, _ in calls))

        self.assertEqual(len(routes), 2)
        fee_pairs = {(route["pools"][0]["fee_tier"], route["pools"][1]["fee_tier"])
                     for route in routes}
        self.assertEqual(fee_pairs, {(100, 3000), (3000, 100)})

        expected = RouteKey(
            chain_id=42161,
            base=BASE.address,
            quote=QUOTE.address,
            pool_a=POOL_100,
            pool_b=POOL_3000,
            fee_a=100,
            fee_b=3000,
            direction="base-to-quote-to-base",
        ).id
        first = next(route for route in routes
                     if route["pools"][0]["fee_tier"] == 100)
        self.assertEqual(first["route_id"], expected)
        self.assertEqual(first["block"], 321)
        self.assertEqual(first["base_symbol"], "BASE")
        self.assertEqual(first["quote_symbol"], "QUOTE")

    def test_missing_registry_symbol_never_calls_rpc(self):
        engine = FakeEngine()
        routes = discover_uniswap_routes_batched(
            engine, ("BASE", "MISSING"), {"BASE": BASE}, 321,
            [100, 500], max_batch=100)
        self.assertEqual(routes, [])
        self.assertEqual(engine.rpc.batches, [])


if __name__ == "__main__":
    unittest.main()
