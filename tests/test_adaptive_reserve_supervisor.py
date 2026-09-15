import json
import os
import tempfile
import unittest

from zero.fork import ForkResult
from zero.ledger import Ledger
from zero.pnl import PnlSwarmSupervisor


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")


class FakeRpc:
    def block_number(self):
        return 777


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()


class TestAdaptiveReserveSupervisor(unittest.TestCase):
    def _config(self):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def _record(self, ledger, predicted, realized, success=True):
        ledger.record_fork_verification(ForkResult(
            block=700,
            strategy="swarm_arbitrage",
            success=success,
            gas_used=100,
            predicted_net=predicted,
            realized_net=realized,
            detail="{}",
        ))

    def test_one_adaptive_reserve_is_shared_by_all_workers_in_block(self):
        cfg = self._config()
        cfg["swarm"]["adaptive_reserve"] = {
            "enabled": True,
            "lookback": 100,
            "min_samples": 2,
            "quantile": 1.0,
            "floor_usd": 0.0,
            "cap_usd": 25.0,
            "bootstrap_reserve_usd": 0.05,
        }
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, "ledger.db"))
            self._record(ledger, 0.10, 0.08, True)
            self._record(ledger, 0.20, 0.0, False)
            supervisor = None

            def runner(worker, allocator, context):
                seen.append(supervisor._cycle_model_reserve_usd)
                return []

            supervisor = PnlSwarmSupervisor(
                FakeEngine(), cfg, ledger,
                worker_runner=runner,
                catalog_builder=lambda block, workers: ([], object()),
            )
            result = supervisor.run_block(777)
            ledger.close()

        self.assertEqual(len(seen), 20)
        self.assertTrue(all(value == seen[0] for value in seen))
        self.assertAlmostEqual(seen[0], 0.20)
        self.assertAlmostEqual(result["adaptive_reserve_usd"], 0.20)
        self.assertEqual(result["reserve_samples"], 2)

    def test_disabled_adaptive_reserve_uses_static_fallback(self):
        cfg = self._config()
        cfg["swarm"]["model_reserve_usd"] = 0.07
        cfg["swarm"]["adaptive_reserve"] = {"enabled": False}
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, "ledger.db"))
            supervisor = None

            def runner(worker, allocator, context):
                seen.append(supervisor._cycle_model_reserve_usd)
                return []

            supervisor = PnlSwarmSupervisor(
                FakeEngine(), cfg, ledger,
                worker_runner=runner,
                catalog_builder=lambda block, workers: ([], object()),
            )
            result = supervisor.run_block(777)
            ledger.close()

        self.assertEqual(len(seen), 20)
        self.assertTrue(all(value == 0.07 for value in seen))
        self.assertAlmostEqual(result["adaptive_reserve_usd"], 0.07)
        self.assertEqual(result["reserve_samples"], 0)


if __name__ == "__main__":
    unittest.main()
