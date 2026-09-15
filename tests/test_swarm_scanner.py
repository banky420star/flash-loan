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
    "name": "BASE->QUOTE fee A | QUOTE->BASE fee B",
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
            "fee_tier": 500,
            "fee_percent": 0.05,
            "decimals0": 6,
            "decimals1": 18,
        },
        {
            "address": POOL_B,
            "token0": BASE,
            "token1": QUOTE,
            "fee_tier": 3000,
            "fee_percent": 0.3,
            "decimals0": 6,
            "decimals1": 18,
        },
    ],
}


class DummyCycle:
    rows = []
    seen_sizes = None

    def __init__(self, *args, **kwargs):
        pass

    def profit_curve(self, state_a, state_b, sizes):
        type(self).seen_sizes = list(sizes)
        return [dict(row) for row in type(self).rows]


class TestSwarmScanner(unittest.TestCase):
    def _context(self, *, base_price=1.0, gas=0.04, premium=5, block=123):
        try:
            from zero.swarm import ScanContext, TokenInfo
        except ImportError as exc:
            self.fail(f"ScanContext is missing: {exc}")
        return ScanContext(
            block=block,
            aave_pool="0x" + "01" * 20,
            oracle="0x" + "02" * 20,
            premium_bps=premium,
            eth_price_usd=2500.0,
            gas_usd=gas,
            tokens={
                "BASE": TokenInfo("BASE", BASE, 6, base_price),
                "QUOTE": TokenInfo("QUOTE", QUOTE, 18, 2500.0),
            },
            pool_states={
                POOL_A: {"sqrtPriceX96": 1, "liquidity": 1},
                POOL_B: {"sqrtPriceX96": 1, "liquidity": 1},
            },
        )

    def _engine(self, size_ladder):
        engine = ShadowEngine.__new__(ShadowEngine)
        engine.config = {
            "swarm": {"size_ladder_usd": size_ladder},
        }
        engine.rpc = object()
        return engine

    def test_swarm_converts_usd_ladder_to_base_and_selects_best_net(self):
        cycle = dict(CYCLE)
        cycle["base_price_usd"] = 100.0
        context = self._context(base_price=100.0, gas=0.5)
        engine = self._engine([100, 200, 400])
        DummyCycle.rows = [
            {"size": 1.0, "hop1_out": 1.0, "hop2_out": 1.02,
             "gross": 0.02, "out_of_range": False},
            {"size": 2.0, "hop1_out": 2.0, "hop2_out": 2.03,
             "gross": 0.03, "out_of_range": False},
            {"size": 4.0, "hop1_out": 4.0, "hop2_out": 4.02,
             "gross": 0.02, "out_of_range": False},
        ]
        with patch("zero.engine.Cycle", DummyCycle):
            row = engine.scan_cycle_config(
                123, cycle, swarm_mode=True,
                model_reserve_usd=0.4, context=context,
            )
        self.assertEqual(DummyCycle.seen_sizes, [1.0, 2.0, 4.0])
        self.assertEqual(row["decision"], "PASS")
        self.assertEqual(row["candidate"]["loan_size"], 2.0)
        self.assertAlmostEqual(row["expected_net_usd"], 2.0)

    def test_tiny_positive_net_is_admitted_and_min_profit_covers_costs(self):
        engine = self._engine([100])
        context = self._context(base_price=1.0, gas=0.04)
        DummyCycle.rows = [
            {"size": 100.0, "hop1_out": 50.0, "hop2_out": 100.1,
             "gross": 0.1, "out_of_range": False},
        ]
        with patch("zero.engine.Cycle", DummyCycle):
            row = engine.scan_cycle_config(
                123, dict(CYCLE), swarm_mode=True,
                model_reserve_usd=0.009, context=context,
            )
        self.assertEqual(row["decision"], "PASS")
        self.assertAlmostEqual(row["expected_net_usd"], 0.001)
        self.assertGreater(row["candidate"]["min_profit"], 0.049)
        self.assertAlmostEqual(row["candidate"]["predicted_net"], 0.001)

    def test_zero_net_is_rejected(self):
        engine = self._engine([100])
        context = self._context(base_price=1.0, gas=0.04)
        DummyCycle.rows = [
            {"size": 100.0, "hop1_out": 50.0, "hop2_out": 100.099,
             "gross": 0.099, "out_of_range": False},
        ]
        with patch("zero.engine.Cycle", DummyCycle):
            row = engine.scan_cycle_config(
                123, dict(CYCLE), swarm_mode=True,
                model_reserve_usd=0.009, context=context,
            )
        self.assertEqual(row["decision"], "REJECT")
        self.assertIsNone(row["candidate"])
        self.assertAlmostEqual(row["expected_net_usd"], 0.0)

    def test_mixed_block_context_is_rejected(self):
        engine = self._engine([100])
        context = self._context(block=122)
        with self.assertRaisesRegex(ValueError, "mixed-block scan context"):
            engine.scan_cycle_config(
                123, dict(CYCLE), swarm_mode=True, context=context,
            )


if __name__ == "__main__":
    unittest.main()
