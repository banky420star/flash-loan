from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestRealizedPnlContract(unittest.TestCase):
    def test_live_candidate_test_writes_realized_and_gas_json(self):
        text = (ROOT / "contracts" / "test" / "ZeroLiveCandidateFork.t.sol").read_text()
        self.assertIn('envString("ZERO_RESULT_PATH")', text)
        self.assertIn("writeFile", text)
        self.assertIn("gasleft()", text)
        self.assertIn("realized_raw", text)
        self.assertIn("gas_used", text)

    def test_foundry_allows_result_directory_write(self):
        text = (ROOT / "foundry.toml").read_text()
        self.assertIn("fs_permissions", text)
        self.assertIn("out/zero-results", text)

    def test_live_script_requires_result_path(self):
        text = (ROOT / "scripts" / "live_candidate_fork_test.sh").read_text()
        self.assertIn("ZERO_RESULT_PATH", text)


if __name__ == "__main__":
    unittest.main()
