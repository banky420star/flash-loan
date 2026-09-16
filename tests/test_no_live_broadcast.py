import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ZERO = ROOT / "zero"


class TestNoLiveBroadcast(unittest.TestCase):
    def test_runtime_has_no_private_key_or_raw_transaction_broadcast_path(self):
        forbidden = (
            "eth_sendRawTransaction",
            "send_raw_transaction",
            "PRIVATE_KEY",
            "ZERO_PRIVATE_KEY",
        )
        text = "\n".join(
            path.read_text(errors="ignore")
            for path in ZERO.rglob("*.py")
        )
        for token in forbidden:
            self.assertNotIn(token, text, token)

    def test_cli_has_no_live_execute_command(self):
        text = (ZERO / "cli.py").read_text()
        self.assertNotIn("live-execute", text)
        self.assertNotIn("broadcast-mainnet", text)


if __name__ == "__main__":
    unittest.main()
