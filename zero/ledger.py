"""SQLite audit ledger: every candidate, traded or not, lands here.

Shadow mode's whole point is the evidence this table accumulates — detection
counts, gate rejections, and (later) simulation outcomes vs predictions.
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
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

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