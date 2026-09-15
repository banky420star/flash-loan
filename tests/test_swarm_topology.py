import json
import os
import unittest


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")


class TestSwarmTopology(unittest.TestCase):
    def _load(self):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def test_builds_four_managers_and_twenty_unique_workers(self):
        try:
            from zero.swarm import build_topology
        except ImportError as exc:
            self.fail(f"build_topology is missing: {exc}")

        cfg = self._load()
        managers, workers = build_topology(cfg)
        self.assertEqual(len(managers), 4)
        self.assertEqual(len(workers), 20)
        self.assertEqual(len({m.manager_id for m in managers}), 4)
        self.assertEqual(len({w.worker_id for w in workers}), 20)
        self.assertEqual(
            {w.worker_id for w in workers},
            {f"{prefix}{n}" for prefix in "ABCD" for n in range(1, 6)},
        )
        self.assertTrue(all(len(m.worker_ids) == 5 for m in managers))

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
