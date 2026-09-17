import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestTuiLaunchers(unittest.TestCase):
    def test_control_and_process_launchers_are_read_only(self):
        expected = {
            "scripts/zero_tui.sh": "python3 -m zero.tui control",
            "scripts/zero_process_tui.sh": "python3 -m zero.tui process",
        }
        for relative, command in expected.items():
            path = ROOT / relative
            self.assertTrue(path.exists(), relative)
            text = path.read_text()
            self.assertIn(command, text)
            self.assertNotIn("PRIVATE_KEY", text)
            self.assertNotIn("send_raw", text)
            self.assertTrue(os.access(path, os.X_OK))

    def test_dev_launcher_exposes_both_tui_modes_and_paths(self):
        text = (ROOT / "scripts/zero_dev.sh").read_text()
        self.assertIn("tui)", text)
        self.assertIn("process-tui)", text)
        self.assertIn("ZERO_RUNTIME_STATUS_PATH", text)
        self.assertIn("ZERO_LOG_PATH", text)
        self.assertIn("python3 -u -m zero.cli swarm", text)
        self.assertIn('tee -a "$ZERO_LOG_PATH"', text)


if __name__ == "__main__":
    unittest.main()
