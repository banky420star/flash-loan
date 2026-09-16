import json
import os
import tempfile
import unittest

from zero.ledger import Ledger
from zero.runtime import RuntimeMonitor


class TestLedgerRuntimeMode(unittest.TestCase):
    def test_ledger_uses_wal_and_busy_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Ledger(os.path.join(tmp,'ledger.db'))
            mode=ledger.conn.execute('PRAGMA journal_mode').fetchone()[0]
            timeout=ledger.conn.execute('PRAGMA busy_timeout').fetchone()[0]
            ledger.close()
        self.assertEqual(mode.lower(),'wal')
        self.assertGreaterEqual(timeout,5000)


class TestRuntimeMonitor(unittest.TestCase):
    def result(self):
        return {
            'block':100,'active_workers':20,'worker_failures':1,
            'routes_scanned':300,'positive_net':2,'best_expected_net':1.5,
            'fork_verifications_attempted':2,'fork_verifications_passed':1,
            'fork_verifications_failed':1,'elapsed_s':4.2,
        }

    def test_cycle_snapshot_has_heartbeat_lag_and_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'status.json')
            monitor=RuntimeMonitor(path,clock=lambda:1000.0)
            status=monitor.record_cycle(
                self.result(),chain_head=103,rpc_endpoint='https://rpc-two')
            with open(path) as handle:
                raw=json.load(handle)
        self.assertEqual(status.block_lag,3)
        self.assertEqual(status.consecutive_errors,0)
        self.assertEqual(raw['heartbeat_at'],1000.0)
        self.assertEqual(raw['positive_net'],2)
        self.assertEqual(raw['fork_passed'],1)
        self.assertEqual(raw['rpc_endpoint'],'https://rpc-two')

    def test_error_counter_resets_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitor=RuntimeMonitor(os.path.join(tmp,'status.json'),clock=lambda:10.0)
            one=monitor.record_error(RuntimeError('boom'))
            two=monitor.record_error(RuntimeError('boom again'))
            self.assertEqual(one.consecutive_errors,1)
            self.assertEqual(two.consecutive_errors,2)
            good=monitor.record_cycle(self.result(),chain_head=100)
            self.assertEqual(good.consecutive_errors,0)
            self.assertIsNone(good.last_error)

    def test_kill_state_is_serialized_but_not_trading_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'status.json')
            monitor=RuntimeMonitor(path,clock=lambda:20.0)
            status=monitor.set_kill_state(True,reason='manual maintenance')
            with open(path) as handle:
                raw=json.load(handle)
        self.assertTrue(status.kill_state)
        self.assertEqual(raw['kill_reason'],'manual maintenance')
        self.assertNotIn('positions',raw)
        self.assertNotIn('nonce',raw)


if __name__=='__main__': unittest.main()
