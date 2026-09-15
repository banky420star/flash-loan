import unittest


ZERO_ADDRESS = "0x" + "00" * 20


def address(n: int) -> str:
    return "0x" + n.to_bytes(20, "big").hex()


class FakeAave:
    def __init__(self):
        symbols = [
            "USDC", "WETH", "WBTC", "tBTC", "wstETH", "rETH", "weETH",
            "ezETH", "rsETH", "ARB", "LINK", "AAVE", "GHO",
        ]
        self.by_address = {address(i + 1): symbol for i, symbol in enumerate(symbols)}
        self.blocks = []

    def pool_address(self, block="latest"):
        self.blocks.append(("pool", block))
        return address(900)

    def oracle_address(self, block="latest"):
        self.blocks.append(("oracle", block))
        return address(901)

    def reserves_list(self, pool, block="latest"):
        self.blocks.append(("reserves", block))
        return list(self.by_address)

    def symbol(self, token, block="latest"):
        self.blocks.append(("symbol", block))
        return self.by_address[token]

    def decimals(self, token, block="latest"):
        self.blocks.append(("decimals", block))
        return 6 if self.by_address[token] in {"USDC"} else 18

    def asset_price(self, oracle, token, block="latest"):
        self.blocks.append(("price", block))
        symbol = self.by_address[token]
        return {"USDC": 1.0, "WETH": 2500.0, "WBTC": 75000.0}.get(symbol, 10.0)


class FakeRpc:
    def __init__(self):
        self.calls = []
        self.pool_by_fee = {
            500: address(500),
            3000: address(3000),
            10000: address(10000),
        }

    def eth_call(self, to, data, block="latest"):
        fee = int(data[-64:], 16)
        self.calls.append((to, fee, block))
        pool = self.pool_by_fee.get(fee, ZERO_ADDRESS)
        return int(pool, 16).to_bytes(32, "big")


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()
        self.config = {
            "chain_id": 42161,
            "uniswap_v3_factory": address(999),
        }


class TestSwarmDiscovery(unittest.TestCase):
    def test_build_token_registry_is_complete_and_pinned(self):
        try:
            from zero.swarm import build_token_registry
        except ImportError as exc:
            self.fail(f"build_token_registry is missing: {exc}")

        aave = FakeAave()
        registry = build_token_registry(aave, block=123)
        expected = {
            "USDC", "WETH", "WBTC", "tBTC", "wstETH", "rETH", "weETH",
            "ezETH", "rsETH", "ARB", "LINK", "AAVE", "GHO",
        }
        self.assertEqual(set(registry), expected)
        self.assertEqual(registry["USDC"].decimals, 6)
        self.assertEqual(registry["WETH"].price_usd, 2500.0)
        self.assertTrue(all(block == 123 for _, block in aave.blocks))

    def test_uniswap_discovery_keeps_existing_pools_and_pins_factory_reads(self):
        from zero.swarm import build_token_registry, discover_uniswap_routes

        aave = FakeAave()
        registry = build_token_registry(aave, block=456)
        engine = FakeEngine()
        routes = discover_uniswap_routes(
            engine,
            ("USDC", "WETH"),
            registry,
            block=456,
            fee_tiers=[100, 500, 3000, 10000],
        )

        self.assertEqual([fee for _, fee, _ in engine.rpc.calls], [100, 500, 3000, 10000])
        self.assertTrue(all(block == 456 for _, _, block in engine.rpc.calls))
        self.assertEqual(len(routes), 6)  # 3 existing pools -> 3 * 2 ordered routes
        self.assertTrue(all(route["block"] == 456 for route in routes))
        self.assertTrue(all(len(route["pools"]) == 2 for route in routes))
        self.assertTrue(all(route["pools"][0]["fee_tier"] != route["pools"][1]["fee_tier"] for route in routes))
        self.assertTrue(all(route["base_decimals"] == 6 for route in routes))
        self.assertTrue(all(route["quote_decimals"] == 18 for route in routes))

    def test_missing_symbol_returns_no_routes_instead_of_crashing(self):
        from zero.swarm import build_token_registry, discover_uniswap_routes

        registry = build_token_registry(FakeAave(), block=789)
        routes = discover_uniswap_routes(
            FakeEngine(), ("USDC.e", "WETH"), registry, block=789,
            fee_tiers=[500, 3000],
        )
        self.assertEqual(routes, [])


if __name__ == "__main__":
    unittest.main()
