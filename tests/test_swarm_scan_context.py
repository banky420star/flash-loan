import unittest


USDC = "0x" + "11" * 20
WETH = "0x" + "22" * 20
POOL_A = "0x" + "aa" * 20
POOL_B = "0x" + "bb" * 20


class FakeAave:
    def __init__(self):
        self.calls = []

    def pool_address(self, block="latest"):
        self.calls.append(("pool_address", block))
        return "0x" + "01" * 20

    def oracle_address(self, block="latest"):
        self.calls.append(("oracle_address", block))
        return "0x" + "02" * 20

    def flashloan_premium_total(self, pool, block="latest"):
        self.calls.append(("premium", block))
        return 5

    def reserves_list(self, pool, block="latest"):
        self.calls.append(("reserves", block))
        return [USDC, WETH]

    def symbol(self, token, block="latest"):
        self.calls.append(("symbol", block))
        return "USDC" if token == USDC else "WETH"

    def decimals(self, token, block="latest"):
        self.calls.append(("decimals", block))
        return 6 if token == USDC else 18

    def asset_price(self, oracle, token, block="latest"):
        self.calls.append(("price", block))
        return 1.0 if token == USDC else 2500.0


class FakePool:
    def __init__(self, address, calls):
        self.address = address
        self.calls = calls

    def fetch_state(self, block="latest"):
        self.calls.append((self.address, block))
        return {"sqrtPriceX96": 2**96, "liquidity": 123456}


class FakeEngine:
    def __init__(self):
        self.aave = FakeAave()
        self.pool_calls = []
        self.config = {
            "arbitrage": {"eth_for_gas": WETH},
        }

    def _gas_cost_usd(self, eth_price):
        self.gas_price_seen = eth_price
        return 0.25

    def _pool(self, pcfg, block="latest"):
        self.pool_calls.append((pcfg["address"], block))
        return FakePool(pcfg["address"], self.pool_calls)


ROUTES = [
    {
        "block": 123,
        "pools": [
            {"address": POOL_A},
            {"address": POOL_B},
        ],
    },
    {
        "block": 123,
        "pools": [
            {"address": POOL_A},
            {"address": POOL_B},
        ],
    },
]


class TestSwarmScanContext(unittest.TestCase):
    def test_context_pins_aave_oracle_and_unique_pool_state_once(self):
        try:
            from zero.swarm import build_scan_context
        except ImportError as exc:
            self.fail(f"build_scan_context is missing: {exc}")

        engine = FakeEngine()
        context = build_scan_context(engine, block=123, route_catalog=ROUTES)
        self.assertEqual(context.block, 123)
        self.assertEqual(context.premium_bps, 5)
        self.assertEqual(context.eth_price_usd, 2500.0)
        self.assertEqual(context.gas_usd, 0.25)
        self.assertEqual(set(context.pool_states), {POOL_A, POOL_B})
        self.assertEqual(set(context.tokens), {"USDC", "WETH"})
        self.assertTrue(all(block == 123 for _, block in engine.aave.calls))
        fetched = [(addr, block) for addr, block in engine.pool_calls if addr in {POOL_A, POOL_B}]
        self.assertEqual(fetched.count((POOL_A, 123)), 2)  # _pool construction + fetch log
        self.assertEqual(fetched.count((POOL_B, 123)), 2)

    def test_context_rejects_route_catalog_from_different_block(self):
        from zero.swarm import build_scan_context

        engine = FakeEngine()
        bad = [{"block": 124, "pools": [{"address": POOL_A}, {"address": POOL_B}]}]
        with self.assertRaisesRegex(ValueError, "mixed-block scan context"):
            build_scan_context(engine, block=123, route_catalog=bad)


if __name__ == "__main__":
    unittest.main()
