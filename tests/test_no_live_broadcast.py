import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ZERO = ROOT / "zero"

# Designated live-broadcast module (user authorized real-money execution
# 2026-09-18). Nothing else in zero/ may carry broadcast/key tokens, and
# nothing below may import it.
LIVE_MODULE = "wallet.py"
SHADOW_PATHS = ("engine.py", "cli.py", "swarm.py", "liquidation_swarm.py",
                "rpc.py", "venues/", "scanner", "ledger")


class TestNoLiveBroadcast(unittest.TestCase):
    def _py_files(self):
        return [p for p in ZERO.rglob("*.py") if p.name != LIVE_MODULE]

    def test_only_designated_module_has_broadcast_path(self):
        forbidden = (
            "eth_sendRawTransaction",
            "send_raw_transaction",
            "PRIVATE_KEY",
            "sign_transaction",
        )
        for path in self._py_files():
            text = path.read_text(errors="ignore")
            for token in forbidden:
                self.assertNotIn(token, text, f"{path}: {token}")

    def test_shadow_paths_do_not_import_wallet(self):
        for path in self._py_files():
            tree = ast.parse(path.read_text(errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(
                        (node.module or "").split(".")[0], "wallet",
                        f"{path} must not import the live wallet module")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(
                            alias.name.split(".")[0], "wallet",
                            f"{path} must not import the live wallet module")

    def test_cli_has_no_live_execute_command(self):
        text = (ZERO / "cli.py").read_text()
        self.assertNotIn("live-execute", text)
        self.assertNotIn("broadcast-mainnet", text)


if __name__ == "__main__":
    unittest.main()