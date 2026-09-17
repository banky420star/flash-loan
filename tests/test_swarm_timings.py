import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zero.ledger import Ledger
from zero.pnl import PnlSwarmSupervisor


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")


class FakeRpc:
    def block_number(self):
        return 999


class FakeAave:
    def __init__(self):
        self.calls = []

    def pool_address(self, block="latest"):
        self.calls.append(("pool", block))
        return "0x" + "01" * 20

    def oracle_address(self, block="latest"):
        self.calls.append(("oracle", block))
        return "0x" + "02" * 20


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()
        self.aave = FakeAave()


class TestSwarmTimingsAndBatchedWiring(unittest.TestCase):
    def _config(self):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        cfg["swarm"]["adaptive_reserve"] = {"enabled": False}
        cfg["swarm"]["max_rpc_batch"] = 17
        cfg["swarm"]["executable_routes_only"] = False
        return cfg

    def test_cycle_output_exposes_non_negative_phase_timings(self):
        cfg = self._config()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, "ledger.db"))
            supervisor = PnlSwarmSupervisor(
                FakeEngine(), cfg, ledger,
                worker_runner=lambda worker, allocator, context: [],
                catalog_builder=lambda block, workers: ([], object()),
            )
            result = supervisor.run_block(999)
            ledger.close()

        for field in ("catalog_ms", "scan_ms", "verify_ms"):
            self.assertIn(field, result)
            self.assertGreaterEqual(result[field], 0.0)
        self.assertLessEqual(
            result["catalog_ms"] + result["scan_ms"] + result["verify_ms"],
            result["elapsed_s"] * 1000 + 5.0,
        )

    def test_default_catalog_builder_uses_batched_helpers_and_configured_limit(self):
        cfg = self._config()
        engine = FakeEngine()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, "ledger.db"))
            supervisor = PnlSwarmSupervisor(engine, cfg, ledger)
            context = object()
            with patch("zero.pnl.build_token_registry_batched", create=True,
                       return_value={}) as registry, \
                 patch("zero.pnl.discover_uniswap_routes_many_batched", create=True,
                       return_value=[]) as discover, \
                 patch("zero.pnl.build_venue_registry", create=True,
                       return_value={}) as venue_registry, \
                 patch("zero.pnl.discover_route_configs", create=True,
                       return_value=[]) as multidex, \
                 patch("zero.pnl.build_scan_context_batched", create=True,
                       return_value=context) as scan_context:
                routes, actual_context = supervisor._build_catalog(
                    999, supervisor.workers)
            ledger.close()

        self.assertEqual(routes, [])
        self.assertIs(actual_context, context)
        self.assertEqual(engine.aave.calls, [("pool", 999), ("oracle", 999)])
        self.assertEqual(registry.call_count, 1)
        self.assertEqual(registry.call_args.kwargs["max_batch"], 17)
        self.assertEqual(registry.call_args.kwargs["pool"], "0x" + "01" * 20)
        self.assertEqual(registry.call_args.kwargs["oracle"], "0x" + "02" * 20)
        self.assertEqual(discover.call_count, 1)
        self.assertEqual(discover.call_args.kwargs["max_batch"], 17)
        self.assertEqual(venue_registry.call_count, 1)
        self.assertGreater(multidex.call_count, 0)
        self.assertEqual(scan_context.call_count, 1)
        self.assertEqual(scan_context.call_args.kwargs["max_batch"], 17)
        self.assertEqual(scan_context.call_args.kwargs["aave_pool"],
                         "0x" + "01" * 20)
        self.assertEqual(scan_context.call_args.kwargs["oracle"],
                         "0x" + "02" * 20)

    def test_static_metadata_is_reused_but_prices_refresh_each_cycle(self):
        cfg = self._config()
        cfg["swarm"]["executable_routes_only"] = True
        cfg["swarm"]["static_metadata_refresh_s"] = 60
        engine = FakeEngine()
        token = SimpleNamespace(symbol="USDC", address="0x" + "03" * 20,
                                decimals=6, price_usd=1.0)
        registry = {"USDC": token}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, "ledger.db"))
            supervisor = PnlSwarmSupervisor(engine, cfg, ledger)
            context = object()
            with patch("zero.pnl.build_token_registry_batched",
                       return_value=registry) as full_registry, \
                 patch("zero.pnl.refresh_token_prices_batched",
                       return_value=registry) as price_refresh, \
                 patch("zero.pnl.discover_uniswap_routes_many_batched",
                       return_value=[]), \
                 patch("zero.pnl.build_venue_registry", return_value={
                     "uniswap_v3": SimpleNamespace(execution_supported=True),
                 }), \
                 patch("zero.pnl.build_scan_context_batched",
                       return_value=context):
                supervisor._build_catalog(999, supervisor.workers)
                supervisor._build_catalog(1000, supervisor.workers)
            ledger.close()

        self.assertEqual(full_registry.call_count, 1)
        self.assertEqual(price_refresh.call_count, 1)
        self.assertEqual(price_refresh.call_args.args[1], 1000)
        self.assertEqual(engine.aave.calls, [("pool", 999), ("oracle", 999)])

    def test_executable_only_hot_path_skips_observe_only_multidex_discovery(self):
        cfg = self._config()
        cfg["swarm"]["executable_routes_only"] = True
        engine = FakeEngine()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, "ledger.db"))
            supervisor = PnlSwarmSupervisor(engine, cfg, ledger)
            context = object()
            with patch("zero.pnl.build_token_registry_batched", return_value={}), \
                 patch("zero.pnl.discover_uniswap_routes_many_batched", return_value=[]), \
                 patch("zero.pnl.build_venue_registry", return_value={
                     "uniswap_v3": SimpleNamespace(execution_supported=True),
                 }), \
                 patch("zero.pnl.discover_route_configs", return_value=[]) as multidex, \
                 patch("zero.pnl.build_scan_context_batched", return_value=context):
                routes, actual_context = supervisor._build_catalog(999, supervisor.workers)
            ledger.close()

        self.assertEqual(routes, [])
        self.assertIs(actual_context, context)
        self.assertEqual(multidex.call_count, 0)


if __name__ == "__main__":
    unittest.main()
