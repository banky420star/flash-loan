import json
import os
import unittest


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")


class TestSwarmTopology(unittest.TestCase):
    def _load(self):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def test_builds_four_managers_and_declared_workers(self):
        try:
            from zero.swarm import build_topology
        except ImportError as exc:
            self.fail(f"build_topology is missing: {exc}")

        cfg = self._load()
        declared = int(cfg["swarm"]["worker_count"])
        managers, workers = build_topology(cfg)
        self.assertEqual(len(managers), 4)
        self.assertEqual(len(workers), declared)
        self.assertEqual(len({m.manager_id for m in managers}), 4)
        self.assertEqual(len({w.worker_id for w in workers}), declared)
        # worker ids must be unique and cover every declared definition
        self.assertEqual(len({w.worker_id for w in workers}), len(workers))
        # every declared worker id appears exactly once
        declared_ids = {w["id"] for m in cfg["swarm"]["managers"]
                        for w in m["workers"]}
        self.assertEqual({w.worker_id for w in workers}, declared_ids)

    def test_every_worker_belongs_to_exactly_one_declared_manager(self):
        from zero.swarm import build_topology

        managers, workers = build_topology(self._load())
        owners = {}
        for manager in managers:
            for worker_id in manager.worker_ids:
                self.assertNotIn(worker_id, owners)
                owners[worker_id] = manager.manager_id
        self.assertEqual(set(owners), {w.worker_id for w in workers})
        for worker in workers:
            self.assertEqual(owners[worker.worker_id], worker.manager_id)
            self.assertEqual(len(worker.primary_pair), 2)

    def test_invalid_declared_counts_are_rejected(self):
        from zero.swarm import build_topology

        cfg = self._load()
        cfg["swarm"]["worker_count"] = 19
        with self.assertRaises(ValueError):
            build_topology(cfg)


if __name__ == "__main__":
    unittest.main()
