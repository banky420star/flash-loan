import unittest
from unittest.mock import patch

from zero.engine import ShadowEngine


BASE = "0x" + "11" * 20
QUOTE = "0x" + "22" * 20
POOL_A = "0x" + "aa" * 20
POOL_B = "0x" + "bb" * 20

CYCLE = {
    "block": 123,
    "route_id": "route-1",
    "name": "BASE->QUOTE 0.3% | QUOTE->BASE 0.01%",
    "base": BASE,
    "quote": QUOTE,
    "base_symbol": "BASE",
    "quote_symbol": "QUOTE",
    "base_decimals": 6,
    "quote_decimals": 18,
    "base_price_usd": 1.0,
    "quote_price_usd": 2500.0,
    "pools": [
        {
            "address": POOL_A,
            "token0": BASE,
            "token1": QUOTE,
            "fee_tier": 3000,
            "fee_percent": 0.3,
            "decimals0": 6,
            "decimals1": 18,
        },
        {
            "address": POOL_B,
            "token0": BASE,
            "token1": QUOTE,
            "fee_tier": 100,
            "fee_percent": 0.01,
            "decimals0": 6,
            "decimals1": 18,
        },
    ],
}


class DummyCycle:
    def __init__(self, *args, **kwargs):
        pass

    def profit_curve(self, state_a, state_b, sizes):
        # Local single-range model says the $25 route has +$0.60 gross.
        return [{
            "size": 25.0,
            "hop1_out": 0.0104,
            "hop2_out": 25.60,
            "gross": 0.60,
            "out_of_range": False,
        }]


class Quote:
    def __init__(self, amount_out):
        self.amount_out = amount_out
        self.initialized_ticks_crossed = 1
        self.gas_estimate = 1


class FakeQuoter:
    def __init__(self):
        self.calls = []

    def quote_exact_input_single(self, **kwargs):
        self.calls.append(kwargs)
        # Exact pinned-block path returns only $25.40 on leg 2. After flash fee
        # and modeled gas this is negative, reproducing the live false-positive
        # class that currently reaches the expensive fork verifier.
        if len(self.calls) == 1:
            return Quote(10_000_000_000_000_000)  # 0.01 QUOTE
        return Quote(25_400_000)  # 25.40 BASE (6 decimals)


class TestExactQuoterPreflight(unittest.TestCase):
    def _context(self):
        from zero.swarm import ScanContext, TokenInfo
        return ScanContext(
            block=123,
            aave_pool="0x" + "01" * 20,
            oracle="0x" + "02" * 20,
            premium_bps=5,
            eth_price_usd=2500.0,
            gas_usd=0.48,
            tokens={
                "BASE": TokenInfo("BASE", BASE, 6, 1.0),
                "QUOTE": TokenInfo("QUOTE", QUOTE, 18, 2500.0),
            },
            pool_states={
                POOL_A: {"sqrtPriceX96": 1, "liquidity": 1},
                POOL_B: {"sqrtPriceX96": 1, "liquidity": 1},
            },
        )

    def test_local_positive_is_rejected_when_exact_quoter_is_negative(self):
        engine = ShadowEngine.__new__(ShadowEngine)
        engine.config = {
            "swarm": {
                "size_ladder_usd": [25],
                "exact_quoter_preflight": True,
            },
        }
        engine.rpc = object()
        engine.quoter = FakeQuoter()

        with patch("zero.engine.Cycle", DummyCycle):
            row = engine.scan_cycle_config(
                123,
                dict(CYCLE),
                swarm_mode=True,
                model_reserve_usd=0.0,
                context=self._context(),
            )

        self.assertEqual(row["decision"], "REJECT")
        self.assertIsNone(row["candidate"])
        self.assertGreater(row["local_expected_net_usd"], 0)
        self.assertLess(row["expected_net_usd"], 0)
        self.assertEqual(row["quote_source"], "quoter_v2")
        self.assertIn("exact_quoter", row["reason"])
        self.assertEqual(len(engine.quoter.calls), 2)
        self.assertEqual(engine.quoter.calls[0]["block"], 123)
        self.assertEqual(engine.quoter.calls[1]["block"], 123)
        self.assertEqual(engine.quoter.calls[0]["fee"], 3000)
        self.assertEqual(engine.quoter.calls[1]["fee"], 100)


if __name__ == "__main__":
    unittest.main()
