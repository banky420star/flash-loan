import unittest
from unittest.mock import patch

from zero.fork_cli import build_status, command_line, run_fork_test


class TestForkCliHelpers(unittest.TestCase):
    @patch("zero.fork.shutil.which")
    def test_status_reports_install_and_exact_block(self, which):
        which.return_value = "/opt/homebrew/bin/anvil"
        status = build_status("https://arb1.arbitrum.io/rpc", 999, port=9545)
        self.assertTrue(status["anvil_installed"])
        self.assertEqual(status["block"], 999)
        self.assertEqual(status["local_rpc"], "http://127.0.0.1:9545")
        self.assertIn("--fork-block-number 999", status["command"])

    def test_command_line_is_shell_quoted(self):
        line = command_line("https://rpc.example/path?a=1&b=2", 123)
        self.assertIn("--fork-block-number 123", line)
        self.assertIn("https://rpc.example/path?a=1&b=2", line)

    @patch("zero.fork_cli.subprocess.run")
    def test_fork_test_passes_rpc_and_block_through_environment(self, run):
        run.return_value.returncode = 0
        rc = run_fork_test("https://arb1.arbitrum.io/rpc", 456)
        self.assertEqual(rc, 0)
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["ARBITRUM_RPC_URL"], "https://arb1.arbitrum.io/rpc")
        self.assertEqual(env["FORK_BLOCK"], "456")
        self.assertEqual(run.call_args.args[0], ["bash", "scripts/fork_test.sh"])


if __name__ == "__main__":
    unittest.main()
