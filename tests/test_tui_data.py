import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from zero.tui_data import (ProcessInfo, load_monitoring_snapshot,
                           load_pnl_summary, parse_ps_line)


SCHEMA = """
CREATE TABLE fork_verifications (
 id INTEGER PRIMARY KEY, ts REAL, block INTEGER, strategy TEXT,
 success INTEGER, gas_used INTEGER, predicted_net REAL,
 realized_net REAL, model_error REAL, outcome_class TEXT,
 detail TEXT, created_at TEXT);
"""


class TestTuiPnlData(unittest.TestCase):
    def test_pnl_separates_execution_evidence_from_harness_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.db")
            conn = sqlite3.connect(path)
            conn.executescript(SCHEMA)
            rows = [
                (100, 1, 1.00, 0.80, -0.20, "measured_success", "swarm_arbitrage"),
                (110, 0, 0.50, 0.00, -0.50, "execution_revert", "swarm_arbitrage"),
                (120, 0, 9.00, 0.00, -9.00, "invalid_harness", "swarm_arbitrage"),
                (130, 1, 2.00, 1.50, -0.50, "measured_success", "swarm_liquidation"),
            ]
            for i, (ts, success, pred, real, err, outcome, strategy) in enumerate(rows, 1):
                conn.execute(
                    "INSERT INTO fork_verifications VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i, ts, 500+i, strategy, success, 1, pred, real, err,
                     outcome, "{}", "2026-09-17 00:00:00"))
            conn.commit()
            conn.close()
            pnl = load_pnl_summary(path, session_start=105.0, now=200.0)

        self.assertAlmostEqual(pnl.realized_total, 2.30)
        self.assertAlmostEqual(pnl.predicted_total, 3.50)
        self.assertAlmostEqual(pnl.day_realized, 2.30)
        self.assertAlmostEqual(pnl.day_predicted, 3.50)
        self.assertAlmostEqual(pnl.session_realized, 1.50)
        self.assertEqual(pnl.measured_successes, 2)
        self.assertEqual(pnl.execution_reverts, 1)
        self.assertEqual(pnl.invalid_harness, 1)
        self.assertAlmostEqual(pnl.by_strategy["swarm_liquidation"].realized, 1.50)

    def test_pnl_refresh_uses_sql_aggregates_and_bounded_curve_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.db")
            conn = sqlite3.connect(path)
            conn.executescript(SCHEMA)
            for i in range(120):
                conn.execute(
                    "INSERT INTO fork_verifications VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i + 1, float(i), 500 + i, "swarm_arbitrage", 1, 1,
                     1.0, 0.5, -0.5, "measured_success", "{}", "now"))
            conn.commit()
            conn.close()
            statements = []
            original = __import__("zero.tui_data", fromlist=["_readonly_connection"])._readonly_connection
            def traced(db_path):
                handle = original(db_path)
                handle.set_trace_callback(statements.append)
                return handle
            with patch("zero.tui_data._readonly_connection", side_effect=traced):
                pnl = load_pnl_summary(path, session_start=50.0, now=200.0)
        upper = [statement.upper() for statement in statements]
        self.assertTrue(any("SUM(" in statement for statement in upper))
        self.assertTrue(any("LIMIT 80" in statement for statement in upper))
        self.assertLessEqual(len(pnl.realized_curve), 80)
        self.assertAlmostEqual(pnl.realized_curve[-1], pnl.realized_total)

    def test_ps_parser_handles_standard_macos_row(self):
        info = parse_ps_line("4242 1 12.5 0.3 01:02:03 S python3 -m zero.cli swarm")
        self.assertEqual(info.pid, 4242)
        self.assertAlmostEqual(info.cpu_percent, 12.5)
        self.assertEqual(info.elapsed_s, 3723)
        self.assertEqual(info.state, "S")
        self.assertIn("zero.cli swarm", info.command)

    def test_stale_reused_status_pid_falls_back_to_real_swarm(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = os.path.join(tmp, "status.json")
            with open(status_path, "w") as handle:
                json.dump({"process_pid": 4242, "process_started_at": 100.0,
                           "heartbeat_at": 190.0}, handle)
            unrelated = ProcessInfo(4242, 1, 0.1, 0.1, 100, "S", "unrelated worker")
            real = ProcessInfo(777, 1, 4.0, 0.2, 20, "S", "python -m zero.cli swarm")
            def reader(pid):
                return unrelated if pid == 4242 else real
            snap = load_monitoring_snapshot(
                status_path=status_path, ledger_path=os.path.join(tmp, "missing.db"),
                log_path=os.path.join(tmp, "missing.log"), now=200.0,
                process_finder=lambda: 777, process_reader=reader)
        self.assertTrue(snap.process_alive)
        self.assertEqual(snap.process.pid, 777)

    def test_snapshot_finds_swarm_when_legacy_status_has_no_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            status_path = os.path.join(tmp, "status.json")
            with open(status_path, "w") as handle:
                json.dump({"heartbeat_at": 190.0}, handle)
            fake = ProcessInfo(777, 1, 3.0, 0.1, 10, "S", "python -m zero.cli swarm")
            snap = load_monitoring_snapshot(
                status_path=status_path, ledger_path=os.path.join(tmp, "missing.db"),
                log_path=os.path.join(tmp, "missing.log"), now=200.0,
                process_finder=lambda: 777, process_reader=lambda pid: fake)
        self.assertTrue(snap.process_alive)
        self.assertEqual(snap.process.pid, 777)

    def test_snapshot_combines_status_process_pnl_and_error_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "ledger.db")
            status_path = os.path.join(tmp, "status.json")
            log_path = os.path.join(tmp, "swarm.log")
            conn = sqlite3.connect(ledger)
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO fork_verifications VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, 180.0, 501, "swarm_arbitrage", 1, 1, 1.0, 0.8,
                 -0.2, "measured_success", "{}", "2026-09-17 00:00:00"))
            conn.commit()
            conn.close()
            with open(status_path, "w") as handle:
                json.dump({"process_pid": 4242, "process_started_at": 100.0,
                           "cycle_started_at": 150.0, "cycle_phase": "scanning",
                           "heartbeat_at": 190.0, "block": 500, "chain_head": 505}, handle)
            with open(log_path, "w") as handle:
                handle.write("normal line\nRPC Error: 403 Forbidden\n")
            fake = ProcessInfo(4242, 1, 25.0, 0.4, 100, "S", "python -m zero.cli swarm")
            snap = load_monitoring_snapshot(
                status_path=status_path, ledger_path=ledger, log_path=log_path,
                now=200.0, process_reader=lambda pid: fake)

        self.assertTrue(snap.process_alive)
        self.assertEqual(snap.heartbeat_age_s, 10.0)
        self.assertEqual(snap.cycle_age_s, 50.0)
        self.assertEqual(snap.status["cycle_phase"], "scanning")
        self.assertAlmostEqual(snap.pnl.session_realized, 0.8)
        self.assertIn("403 Forbidden", snap.errors[-1])


if __name__ == "__main__":
    unittest.main()
