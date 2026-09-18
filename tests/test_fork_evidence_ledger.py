import os
import sqlite3
import tempfile
import unittest

from zero.fork import ForkResult
from zero.ledger import Ledger


LEGACY_SCHEMA = """
CREATE TABLE fork_verifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ts REAL NOT NULL, block INTEGER NOT NULL, strategy TEXT NOT NULL,
 success INTEGER NOT NULL, gas_used INTEGER NOT NULL,
 predicted_net REAL NOT NULL, realized_net REAL NOT NULL,
 model_error REAL NOT NULL, detail TEXT, created_at TEXT
);
"""


class TestForkEvidenceLedger(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="zero-evidence-", suffix=".db")
        os.close(fd)

    def tearDown(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def seed_legacy(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(LEGACY_SCHEMA)
        rows = [
            (1.0, 100, "swarm_arbitrage", 0, 0, 0.05, 0.0, -0.05,
             '{"stdout_tail":"[FAIL: StepFailed(2, 0x1234)]"}', "old"),
            (2.0, 101, "swarm_arbitrage", 0, 0, 0.04, 0.0, -0.04,
             '{"stderr_tail":"ERROR: forge is required"}', "old"),
            (3.0, 102, "swarm_arbitrage", 1, 100, 0.03, 0.02, -0.01,
             '{}', "old"),
        ]
        conn.executemany(
            "INSERT INTO fork_verifications "
            "(ts,block,strategy,success,gas_used,predicted_net,realized_net,"
            "model_error,detail,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

    def test_legacy_database_is_migrated_and_only_real_evidence_is_learned(self):
        self.seed_legacy()
        ledger = Ledger(self.path)
        try:
            columns = [r[1] for r in ledger.conn.execute(
                "PRAGMA table_info(fork_verifications)").fetchall()]
            self.assertIn("outcome_class", columns)
            all_rows = ledger.fork_economics(
                limit=10, reserve_eligible_only=False)
            self.assertEqual([r["outcome_class"] for r in all_rows], [
                "measured_success", "infrastructure_error",
                "execution_revert"])
            learned = ledger.fork_economics(limit=10)
            self.assertEqual([r["outcome_class"] for r in learned], [
                "measured_success", "execution_revert"])
        finally:
            ledger.close()

    def test_new_outcome_class_is_persisted(self):
        ledger = Ledger(self.path)
        try:
            result = ForkResult(
                block=123, strategy="swarm_arbitrage", success=False,
                gas_used=0, predicted_net=0.1, realized_net=0.0,
                outcome_class="infrastructure_error")
            ledger.record_fork_verification(result)
            rows = ledger.fork_economics(
                limit=10, reserve_eligible_only=False)
            self.assertEqual(rows[0]["outcome_class"], "infrastructure_error")
            self.assertEqual(ledger.fork_economics(limit=10), [])
        finally:
            ledger.close()


if __name__ == "__main__":
    unittest.main()
