import os
import unittest
from unittest.mock import patch

from zero import cli


class TestEnvironmentConfig(unittest.TestCase):
    def test_rpc_and_ledger_can_be_overridden_by_environment(self):
        env = {
            "ZERO_RPC_URL": "https://rpc.example.invalid",
            "ZERO_LEDGER_PATH": "/tmp/zero-test-ledger.db",
            "ZERO_MAX_RPC_BATCH": "12",
            "ZERO_MAX_RPC_CONCURRENCY": "7",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = cli.load_config()
        self.assertEqual(cfg["rpc_url"], env["ZERO_RPC_URL"])
        self.assertEqual(cfg["ledger_path"], env["ZERO_LEDGER_PATH"])
        self.assertEqual(cfg["swarm"]["max_rpc_batch"], 12)
        self.assertEqual(cfg["swarm"]["max_rpc_concurrency"], 7)

    def test_rpc_url_list_can_be_overridden_in_order(self):
        env = {
            "ZERO_RPC_URLS": "https://one.example, https://two.example",
            "ZERO_RPC_COOLDOWN_S": "9",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.dict(os.environ, {"ZERO_RPC_URL": ""}):
                cfg = cli.load_config()
        self.assertEqual(cfg["rpc_urls"], [
            "https://one.example", "https://two.example"])
        self.assertEqual(cfg["rpc_url"], "https://one.example")
        self.assertEqual(cfg["rpc_cooldown_s"], 9.0)

    def test_legacy_single_rpc_override_collapses_pool(self):
        env = {"ZERO_RPC_URL": "https://single.example", "ZERO_RPC_URLS": ""}
        with patch.dict(os.environ, env, clear=False):
            cfg = cli.load_config()
        self.assertEqual(cfg["rpc_urls"], ["https://single.example"])


if __name__ == "__main__":
    unittest.main()
