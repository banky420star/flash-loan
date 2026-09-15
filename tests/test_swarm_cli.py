import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import zero.cli as cli


class FakeSupervisor:
    def __init__(self, results=None):
        self.results = list(results or [{
            "block": 777,
            "active_workers": 20,
            "routes_scanned": 12,
            "worker_failures": 0,
            "positive_net": 1,
            "best_expected_net": 0.03,
            "fork_verifications_attempted": 1,
            "fork_verifications_passed": 1,
            "fork_verifications_failed": 0,
            "candidates": [],
        }])
        self.calls = 0

    def run_block(self):
        self.calls += 1
        if not self.results:
            raise KeyboardInterrupt
        return self.results.pop(0)


class TestSwarmCli(unittest.TestCase):
    def test_swarm_once_prints_structured_result(self):
        supervisor = FakeSupervisor()
        out = io.StringIO()
        if not hasattr(cli, "_swarm_supervisor"):
            self.fail("CLI swarm supervisor factory is missing")
        with patch.object(cli, "_swarm_supervisor", return_value=supervisor), redirect_stdout(out):
            code = cli.main(["swarm-once"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["block"], 777)
        self.assertEqual(payload["active_workers"], 20)
        self.assertEqual(supervisor.calls, 1)

    def test_swarm_command_exists_and_accepts_interval(self):
        supervisor = FakeSupervisor()
        out = io.StringIO()
        with patch.object(cli, "_swarm_supervisor", return_value=supervisor), \
             patch.object(cli.time, "sleep", side_effect=KeyboardInterrupt), \
             redirect_stdout(out):
            code = cli.main(["swarm", "--interval", "0.01"])
        self.assertEqual(code, 0)
        self.assertEqual(supervisor.calls, 1)
        self.assertIn("block 777", out.getvalue())
        self.assertIn("workers=20", out.getvalue())

    def test_swarm_factory_wires_exact_fork_verifier(self):
        sentinel = object()
        cfg = {
            "rpc_url": "https://example.invalid",
            "ledger_path": ":memory:",
            "aave_provider": "0x" + "11" * 20,
            "swarm": {},
        }
        with patch.object(cli, "load_config", return_value=cfg), \
             patch.object(cli, "Ledger") as ledger_cls, \
             patch.object(cli, "ShadowEngine") as engine_cls, \
             patch.object(cli, "SwarmSupervisor", return_value=sentinel) as supervisor_cls:
            ledger = ledger_cls.return_value
            engine = engine_cls.return_value
            result = cli._swarm_supervisor()
        self.assertIs(result, sentinel)
        args, kwargs = supervisor_cls.call_args
        self.assertIs(args[0], engine)
        self.assertIs(args[1], cfg)
        self.assertIs(args[2], ledger)
        verifier = kwargs["verifier"]
        with patch.object(cli, "run_live_candidate_fork", return_value=7) as run:
            self.assertEqual(verifier({"candidate": {}, "steps": []}), 7)
        run.assert_called_once_with(cfg["rpc_url"], {"candidate": {}, "steps": []})


if __name__ == "__main__":
    unittest.main()
