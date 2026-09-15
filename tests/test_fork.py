import unittest
from unittest.mock import patch

from zero.fork import (
    AnvilFork,
    ForkSafetyError,
    ForkResult,
    assert_local_write_target,
    is_loopback_rpc,
)


class TestForkSafety(unittest.TestCase):
    def test_loopback_urls_are_allowed(self):
        self.assertTrue(is_loopback_rpc("http://127.0.0.1:8545"))
        self.assertTrue(is_loopback_rpc("http://localhost:9545"))
        self.assertTrue(is_loopback_rpc("http://[::1]:8545"))

    def test_remote_rpc_is_not_loopback(self):
        self.assertFalse(is_loopback_rpc("https://arb1.arbitrum.io/rpc"))
        self.assertFalse(is_loopback_rpc("https://example.com"))

    def test_remote_write_target_is_rejected(self):
        with self.assertRaises(ForkSafetyError):
            assert_local_write_target("https://arb1.arbitrum.io/rpc")

    def test_local_write_target_is_accepted(self):
        self.assertEqual(
            assert_local_write_target("http://127.0.0.1:8545"),
            "http://127.0.0.1:8545",
        )


class TestAnvilFork(unittest.TestCase):
    def test_exact_block_command(self):
        fork = AnvilFork(
            upstream_rpc="https://arb1.arbitrum.io/rpc",
            block_number=123456789,
            port=9545,
        )
        self.assertEqual(
            fork.command(),
            [
                "anvil",
                "--fork-url", "https://arb1.arbitrum.io/rpc",
                "--fork-block-number", "123456789",
                "--host", "127.0.0.1",
                "--port", "9545",
                "--chain-id", "42161",
                "--silent",
            ],
        )
        self.assertEqual(fork.local_rpc_url, "http://127.0.0.1:9545")

    @patch("zero.fork.shutil.which")
    def test_installed_uses_path_lookup(self, which):
        which.return_value = "/usr/local/bin/anvil"
        self.assertTrue(AnvilFork("https://upstream", 1).installed())
        which.assert_called_once_with("anvil")

    @patch("zero.fork.shutil.which")
    def test_missing_anvil_is_reported(self, which):
        which.return_value = None
        self.assertFalse(AnvilFork("https://upstream", 1).installed())


class TestForkResult(unittest.TestCase):
    def test_result_round_trip_dict(self):
        result = ForkResult(
            block=123,
            strategy="arbitrage",
            success=True,
            gas_used=456789,
            predicted_net=12.5,
            realized_net=11.75,
            detail="ok",
        )
        self.assertEqual(result.model_error, -0.75)
        self.assertEqual(result.as_dict()["gas_used"], 456789)
        self.assertEqual(result.as_dict()["model_error"], -0.75)


class TestAnvilLifecycle(unittest.TestCase):
    @patch("zero.fork.subprocess.Popen")
    @patch("zero.fork.shutil.which")
    def test_start_launches_exact_command(self, which, popen):
        which.return_value = "/usr/local/bin/anvil"
        proc = object()
        popen.return_value = proc
        fork = AnvilFork("https://upstream", 42)
        self.assertIs(fork.start(), proc)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], fork.command())


if __name__ == "__main__":
    unittest.main()
