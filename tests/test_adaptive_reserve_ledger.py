import os
import tempfile
import unittest

from zero.fork import ForkResult
from zero.ledger import Ledger


class TestAdaptiveReserveLedger(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="zero-reserve-", suffix=".db")
        os.close(fd)
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.ledger.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def _record(self, predicted, realized, success=True):
        self.ledger.record_fork_verification(ForkResult(
            block=123,
            strategy="swarm_arbitrage",
            success=success,
            gas_used=100,
            predicted_net=predicted,
            realized_net=realized,
            detail="{}",
        ))

    def test_fork_economics_returns_newest_first(self):
        self._record(1.0, 0.8, True)
        self._record(0.5, 0.0, False)
        rows = self.ledger.fork_economics(limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["success"], False)
        self.assertAlmostEqual(rows[0]["predicted_net"], 0.5)
        self.assertAlmostEqual(rows[0]["realized_net"], 0.0)
        self.assertAlmostEqual(rows[0]["model_error"], -0.5)
        self.assertEqual(rows[1]["success"], True)
        self.assertAlmostEqual(rows[1]["model_error"], -0.2)

    def test_fork_economics_respects_limit(self):
        self._record(1.0, 0.9, True)
        self._record(2.0, 1.7, True)
        self._record(3.0, 2.5, True)
        rows = self.ledger.fork_economics(limit=2)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]["predicted_net"], 3.0)
        self.assertAlmostEqual(rows[1]["predicted_net"], 2.0)

    def test_fork_economics_rejects_non_positive_limit(self):
        with self.assertRaises(ValueError):
            self.ledger.fork_economics(limit=0)


if __name__ == "__main__":
    unittest.main()
