import json
import os
import threading
import unittest


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")


class FakeRpc:
    def block_number(self):
        return 888


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()


class ThreadRecordingLedger:
    def __init__(self):
        self.thread_names = []
        self.rows = []

    def record(self, **kwargs):
        self.thread_names.append(threading.current_thread().name)
        self.rows.append(kwargs)
        return len(self.rows)


class TestSwarmLedger(unittest.TestCase):
    def _config(self):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def test_workers_return_rows_but_only_supervisor_writes_ledger(self):
        try:
            from zero.swarm import SwarmSupervisor
        except ImportError as exc:
            self.fail(f"SwarmSupervisor is missing: {exc}")

        ledger = ThreadRecordingLedger()
        main_thread = threading.current_thread().name

        def runner(worker, allocator, context):
            if worker.worker_id != "A1":
                return []
            return [{
                "block": 888,
                "route_id": "route-1",
                "worker_id": worker.worker_id,
                "manager_id": worker.manager_id,
                "size": 100.0,
                "gross": 0.2,
                "gross_usd": 0.2,
                "flash_fee_usd": 0.05,
                "gas_usd": 0.04,
                "model_reserve_usd": 0.01,
                "expected_net_usd": 0.10,
                "decision": "PASS",
                "reason": "ok",
                "candidate": {"block": 888, "predicted_net": 0.10},
            }]

        supervisor = SwarmSupervisor(
            FakeEngine(), self._config(), ledger,
            worker_runner=runner,
            catalog_builder=lambda block, workers: ([], object()),
        )
        result = supervisor.run_block(888)
        self.assertEqual(result["positive_net"], 1)
        self.assertEqual(len(ledger.rows), 1)
        self.assertEqual(ledger.thread_names, [main_thread])
        self.assertEqual(ledger.rows[0]["strategy"], "swarm_arbitrage")
        self.assertEqual(ledger.rows[0]["decision"], "PASS")


if __name__ == "__main__":
    unittest.main()
