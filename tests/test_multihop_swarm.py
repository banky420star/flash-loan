import json
import os
import tempfile
import unittest

from zero.ledger import Ledger
from zero.swarm import ScanContext, SwarmSupervisor
from zero.venues.base import PoolRef


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")
A = "0x" + "11" * 20
B = "0x" + "22" * 20
C = "0x" + "33" * 20
P1 = "0x" + "a1" * 20
P2 = "0x" + "a2" * 20
P3 = "0x" + "a3" * 20
P4 = "0x" + "a4" * 20


class FakeRpc:
    def block_number(self):
        return 777


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()
        self.calls = []

    def scan_multihop_route(self, block, route, **kwargs):
        self.calls.append((block, route["route_id"]))
        return {
            "block": block, "route_id": route["route_id"],
            "size": 100.0, "loan_notional_usd": 100.0,
            "gross_usd": 2.0, "flash_fee_usd": 0.1,
            "gas_usd": 0.2, "model_reserve_usd": 0.1,
            "expected_net_usd": 1.6, "decision": "OBSERVE",
            "reason": "execution_encoder_unavailable",
            "candidate": {
                "block": block, "route_id": route["route_id"],
                "route_kind": "multihop_exact", "base_asset": A,
                "loan_size": 100.0, "predicted_net": 1.6,
                "min_profit": 0.0, "executable": False,
                "legs": route["legs"],
            },
        }


class TestMultiHopSwarm(unittest.TestCase):
    def config(self):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        cfg["swarm"]["verify_positive_candidates"] = True
        return cfg

    def context(self):
        return ScanContext(777, "pool", "oracle", 5, 2000.0, 0.1, {}, {})

    def test_profitable_three_leg_route_enters_book_but_is_not_verified(self):
        cfg = self.config()
        route = {
            "block": 777, "route_kind": "multihop_exact",
            "route_id": "route-3hop", "base_symbol": "USDC",
            "quote_symbol": "WETH", "legs": [{}, {}, {}],
        }
        verifier_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, "ledger.db"))
            supervisor = SwarmSupervisor(
                FakeEngine(), cfg, ledger,
                verifier=lambda payload: verifier_calls.append(payload) or 0,
                catalog_builder=lambda block, workers: ([route], self.context()),
            )
            result = supervisor.run_block(777)
            ledger.close()

        self.assertEqual(result["positive_net"], 1)
        self.assertEqual(result["fork_verifications_attempted"], 0)
        self.assertEqual(verifier_calls, [])
        self.assertEqual(supervisor.engine.calls, [(777, "route-3hop")])

    def test_multihop_config_builder_caps_three_leg_routes(self):
        from zero.routes import build_multihop_route_configs

        def make_pool(venue, address, x, y):
            first, second = sorted((x, y), key=lambda value: int(value, 16))
            return PoolRef(venue, address, first, second, 500, "uniswap_v3")

        pools = [
            make_pool("uniswap_v3", P1, A, B),
            make_pool("sushi_v3", P2, B, C),
            make_pool("camelot_v3", P3, C, A),
            make_pool("sushi_v3", P4, A, B),
        ]
        class Token:
            def __init__(self, address, decimals, price_usd):
                self.address = address
                self.decimals = decimals
                self.price_usd = price_usd

        tokens = {
            "USDC": Token(A, 6, 1.0),
            "WETH": Token(B, 18, 2000.0),
            "ARB": Token(C, 18, 1.0),
        }
        rows = build_multihop_route_configs(
            42161, ("USDC", "WETH"), tokens, pools,
            block=777, max_routes=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["route_kind"] == "multihop_exact" for row in rows))
        self.assertTrue(all(len(row["legs"]) == 3 for row in rows))
        self.assertEqual(len({row["route_id"] for row in rows}), 2)


if __name__ == "__main__":
    unittest.main()
