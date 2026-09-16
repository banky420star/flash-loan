"""SQLite audit ledger: every candidate, traded or not, lands here.

Shadow mode's whole point is the evidence this table accumulates — detection
counts, gate rejections, and simulation outcomes vs predictions.
"""

import json
import sqlite3
import time


SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    block INTEGER,
    strategy TEXT NOT NULL,
    asset TEXT,
    loan_size REAL,
    gross_profit REAL,
    net_profit REAL,
    min_profit REAL,
    decision TEXT NOT NULL,
    reason TEXT,
    detail TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS fork_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    block INTEGER NOT NULL,
    strategy TEXT NOT NULL,
    success INTEGER NOT NULL,
    gas_used INTEGER NOT NULL,
    predicted_net REAL NOT NULL,
    realized_net REAL NOT NULL,
    model_error REAL NOT NULL,
    outcome_class TEXT,
    detail TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    block INTEGER,
    detected INTEGER,
    passed INTEGER,
    rejected INTEGER,
    note TEXT
);
"""


class Ledger:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, timeout=5.0)
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate_fork_outcomes()

    def _migrate_fork_outcomes(self) -> None:
        columns = {row[1] for row in self.conn.execute(
            "PRAGMA table_info(fork_verifications)").fetchall()}
        if "outcome_class" not in columns:
            self.conn.execute(
                "ALTER TABLE fork_verifications ADD COLUMN outcome_class TEXT")

        rows = self.conn.execute(
            "SELECT id, success, detail FROM fork_verifications "
            "WHERE outcome_class IS NULL OR outcome_class = ''").fetchall()
        for row_id, success, detail in rows:
            if bool(success):
                outcome = "measured_success"
            else:
                text = detail or ""
                if "StepFailed(" in text or "MinimumProfitNotMet(" in text:
                    outcome = "execution_revert"
                else:
                    outcome = "invalid_harness"
            self.conn.execute(
                "UPDATE fork_verifications SET outcome_class=? WHERE id=?",
                (outcome, row_id))
        self.conn.commit()

    def record(self, *, block: int, strategy: str, decision: str,
               asset: str | None = None, loan_size: float | None = None,
               gross: float | None = None, net: float | None = None,
               min_profit: float | None = None, reason: str | None = None,
               detail: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO opportunities (ts, block, strategy, asset, loan_size,"
            " gross_profit, net_profit, min_profit, decision, reason, detail,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), block, strategy, asset, loan_size, gross, net,
             min_profit, decision, reason,
             json.dumps(detail) if detail else None,
             time.strftime("%Y-%m-%d %H:%M:%S")))
        self.conn.commit()
        return cur.lastrowid

    def record_fork_verification(self, result) -> int:
        cur = self.conn.execute(
            "INSERT INTO fork_verifications "
            "(ts, block, strategy, success, gas_used, predicted_net, "
            "realized_net, model_error, outcome_class, detail, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), result.block, result.strategy, int(result.success),
             result.gas_used, result.predicted_net, result.realized_net,
             result.model_error, result.outcome_class, result.detail,
             time.strftime("%Y-%m-%d %H:%M:%S")))
        self.conn.commit()
        return cur.lastrowid

    def fork_tail(self, n: int = 20) -> list:
        rows = self.conn.execute(
            "SELECT block, strategy, success, gas_used, predicted_net, "
            "realized_net, model_error, detail, created_at "
            "FROM fork_verifications ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [{
            "block": r[0], "strategy": r[1], "success": bool(r[2]),
            "gas_used": r[3], "predicted_net": r[4], "realized_net": r[5],
            "model_error": r[6], "detail": r[7], "created_at": r[8],
        } for r in rows]

    def fork_economics(self, limit: int = 100, *,
                       reserve_eligible_only: bool = True) -> list:
        """Return recent economics, excluding non-execution evidence by default."""
        limit = int(limit)
        if limit <= 0:
            raise ValueError("limit must be positive")
        where = (
            "WHERE outcome_class IN ('measured_success','execution_revert') "
            if reserve_eligible_only else ""
        )
        rows = self.conn.execute(
            "SELECT success, predicted_net, realized_net, model_error, "
            "outcome_class FROM fork_verifications " + where +
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{
            "success": bool(row[0]),
            "predicted_net": row[1],
            "realized_net": row[2],
            "model_error": row[3],
            "outcome_class": row[4],
        } for row in rows]

    def record_cycle(self, *, block: int, detected: int, passed: int,
                     rejected: int, note: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO cycles (ts, block, detected, passed, rejected, note)"
            " VALUES (?,?,?,?,?,?)",
            (time.time(), block, detected, passed, rejected, note))
        self.conn.commit()
        return cur.lastrowid

    def tail(self, n: int = 20) -> list:
        rows = self.conn.execute(
            "SELECT created_at, strategy, decision, reason, loan_size,"
            " net_profit, min_profit FROM opportunities"
            " ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [{"created_at": r[0], "strategy": r[1], "decision": r[2],
                 "reason": r[3], "loan_size": r[4], "net": r[5],
                 "min_profit": r[6]} for r in rows]

    def stats(self) -> dict:
        rows = self.conn.execute(
            "SELECT strategy, decision, COUNT(*) FROM opportunities"
            " GROUP BY strategy, decision").fetchall()
        out: dict = {}
        for strategy, decision, count in rows:
            out.setdefault(strategy, {})[decision] = count
        return out

    def close(self):
        self.conn.close()
