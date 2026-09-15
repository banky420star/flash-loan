import tempfile
import unittest

from zero.fork import ForkResult
from zero.ledger import Ledger


class TestForkLedger(unittest.TestCase):
    def test_records_and_reads_fork_verification(self):
        with tempfile.NamedTemporaryFile() as f:
            ledger = Ledger(f.name)
            result = ForkResult(
                block=777,
                strategy="arbitrage",
                success=True,
                gas_used=321000,
                predicted_net=9.5,
                realized_net=8.75,
                detail="fork ok",
            )
            row_id = ledger.record_fork_verification(result)
            self.assertGreater(row_id, 0)
            rows = ledger.fork_tail(1)
            self.assertEqual(rows[0]["block"], 777)
            self.assertEqual(rows[0]["strategy"], "arbitrage")
            self.assertEqual(rows[0]["gas_used"], 321000)
            self.assertAlmostEqual(rows[0]["model_error"], -0.75)
            self.assertTrue(rows[0]["success"])
            ledger.close()


if __name__ == "__main__":
    unittest.main()
