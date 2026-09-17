import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestBaselineGuard(unittest.TestCase):
    def test_runtime_wal_files_are_ignored(self):
        ignore = (ROOT / ".gitignore").read_text()
        self.assertIn("*.db-shm", ignore)
        self.assertIn("*.db-wal", ignore)


if __name__ == "__main__":
    unittest.main()
