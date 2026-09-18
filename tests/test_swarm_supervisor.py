import json
import os
import threading
import time
import unittest


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")


class FakeRpc:
    def block_number(self):
        return 777


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()


class FakeLedger:
    def __init__(self):
        self.rows = []

    def record(self, **kwargs):
        self.rows.append(kwargs)
        return len(self.rows)


class TestSwarmSupervisor(unittest.TestCase):
    def _config(self):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def test_twenty_worker_tasks_respect_concurrency_cap(self):
        try:
            from zero.swarm import SwarmSupervisor
        except ImportError as exc:
            self.fail(f"SwarmSupervisor is missing: {exc}")

        cfg = self._config()
        cfg["swarm"]["max_rpc_concurrency"] = 4
        current = 0
        maximum = 0
        lock = threading.Lock()
        seen = []

        def runner(worker, allocator, context):
            nonlocal current, maximum
            with lock:
                current += 1
                maximum = max(maximum, current)
                seen.append(worker.worker_id)
            time.sleep(0.01)
            with lock:
                current -= 1
            return []

        supervisor = SwarmSupervisor(
            FakeEngine(), cfg, FakeLedger(),
            worker_runner=runner,
            catalog_builder=lambda block, workers: ([], object()),
        )
        result = supervisor.run_block(777)
        self.assertEqual(result["active_workers"], 25)
        self.assertEqual(set(seen),
                         {w["id"] for m in cfg["swarm"]["managers"]
                          for w in m["workers"]})
        self.assertGreater(maximum, 1)
        self.assertLessEqual(maximum, 4)

    def test_worker_failure_is_isolated(self):
        from zero.swarm import SwarmSupervisor

        cfg = self._config()

        def runner(worker, allocator, context):
            if worker.worker_id == "A1":
                raise RuntimeError("boom")
            return []

        result = SwarmSupervisor(
            FakeEngine(), cfg, FakeLedger(),
            worker_runner=runner,
            catalog_builder=lambda block, workers: ([], object()),
        ).run_block(777)
        self.assertEqual(result["active_workers"], 25)
        self.assertEqual(result["worker_failures"], 1)
        self.assertEqual(result["candidates"], [])

    def test_duplicate_catalog_routes_are_suppressed(self):
        from zero.swarm import SwarmSupervisor

        cfg = self._config()
        route = {
            "block": 777,
            "route_id": "same-route",
            "base_symbol": "USDC",
            "quote_symbol": "WETH",
        }
        supervisor = SwarmSupervisor(
            FakeEngine(), cfg, FakeLedger(),
            worker_runner=lambda worker, allocator, context: [],
            catalog_builder=lambda block, workers: ([dict(route), dict(route)], object()),
        )
        result = supervisor.run_block(777)
        self.assertEqual(result["duplicates_suppressed"], 1)

    def test_idle_worker_can_steal_from_another_primary_queue(self):
        from zero.swarm import WorkAllocator, WorkerSpec

        workers = [
            WorkerSpec("A1", "ALPHA", ("USDC", "WETH"), "core"),
            WorkerSpec("D1", "DELTA", ("ARB", "WETH"), "dynamic"),
        ]
        routes = [
            {"route_id": "u1", "base_symbol": "USDC", "quote_symbol": "WETH"},
            {"route_id": "u2", "base_symbol": "USDC", "quote_symbol": "WETH"},
        ]
        allocator = WorkAllocator(workers, routes)
        stolen = allocator.next_route("D1", allow_steal=True)
        self.assertIsNotNone(stolen)
        self.assertIn(stolen["route_id"], {"u1", "u2"})


if __name__ == "__main__":
    unittest.main()
