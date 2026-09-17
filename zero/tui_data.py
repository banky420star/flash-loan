from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import time


@dataclass(frozen=True)
class StrategyPnl:
    predicted: float = 0.0
    realized: float = 0.0
    model_error: float = 0.0
    count: int = 0


@dataclass(frozen=True)
class PnlSummary:
    predicted_total: float = 0.0
    realized_total: float = 0.0
    model_error_total: float = 0.0
    session_predicted: float = 0.0
    session_realized: float = 0.0
    day_predicted: float = 0.0
    day_realized: float = 0.0
    measured_successes: int = 0
    execution_reverts: int = 0
    invalid_harness: int = 0
    infrastructure_errors: int = 0
    best_realized: float | None = None
    worst_realized: float | None = None
    realized_curve: tuple[float, ...] = ()
    by_strategy: dict[str, StrategyPnl] = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    cpu_percent: float
    memory_percent: float
    elapsed_s: int
    state: str
    command: str
    alive: bool = True


def _elapsed_seconds(value: str) -> int:
    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        days = int(day_text)
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, minutes, seconds = 0, parts[0], parts[1]
    else:
        hours, minutes, seconds = 0, 0, parts[0]
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_ps_line(line: str) -> ProcessInfo:
    parts = line.strip().split(None, 6)
    if len(parts) != 7:
        raise ValueError("unexpected ps output")
    pid, ppid, cpu, memory, elapsed, state, command = parts
    return ProcessInfo(
        pid=int(pid),
        ppid=int(ppid),
        cpu_percent=float(cpu),
        memory_percent=float(memory),
        elapsed_s=_elapsed_seconds(elapsed),
        state=state,
        command=command,
    )


def _readonly_connection(path: str) -> sqlite3.Connection | None:
    if not os.path.exists(path):
        return None
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=1.0)


def load_pnl_summary(path: str, *, session_start: float | None = None,
                     now: float | None = None) -> PnlSummary:
    conn = _readonly_connection(path)
    if conn is None:
        return PnlSummary()
    current = float(time.time() if now is None else now)
    local = time.localtime(current)
    day_start = time.mktime((local.tm_year, local.tm_mon, local.tm_mday,
                             0, 0, 0, local.tm_wday, local.tm_yday, local.tm_isdst))
    session_cutoff = (float(session_start) if session_start is not None
                      else current + 1.0)
    eligible = "COALESCE(outcome_class,'invalid_harness') IN ('measured_success','execution_revert')"
    try:
        agg = conn.execute(f"""
            SELECT
              COALESCE(SUM(CASE WHEN {eligible} THEN predicted_net ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN {eligible} THEN realized_net ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN {eligible} THEN model_error ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN {eligible} AND ts >= ? THEN predicted_net ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN {eligible} AND ts >= ? THEN realized_net ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN {eligible} AND ts >= ? THEN predicted_net ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN {eligible} AND ts >= ? THEN realized_net ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN outcome_class='measured_success' AND success=1 THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN outcome_class='execution_revert' THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN COALESCE(outcome_class,'invalid_harness')='invalid_harness' THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN outcome_class='infrastructure_error' THEN 1 ELSE 0 END),0),
              MAX(CASE WHEN {eligible} THEN realized_net END),
              MIN(CASE WHEN {eligible} THEN realized_net END)
            FROM fork_verifications
        """, (session_cutoff, session_cutoff, day_start, day_start)).fetchone()
        strategy_rows = conn.execute(f"""
            SELECT strategy, COALESCE(SUM(predicted_net),0),
                   COALESCE(SUM(realized_net),0), COALESCE(SUM(model_error),0), COUNT(*)
            FROM fork_verifications WHERE {eligible}
            GROUP BY strategy ORDER BY strategy
        """).fetchall()
        curve_rows = conn.execute(f"""
            SELECT realized_net FROM (
              SELECT id, realized_net FROM fork_verifications
              WHERE {eligible} ORDER BY id DESC LIMIT 80
            ) ORDER BY id ASC
        """).fetchall()
    except sqlite3.Error:
        return PnlSummary()
    finally:
        conn.close()

    strategies = {
        str(row[0]): StrategyPnl(float(row[1]), float(row[2]), float(row[3]), int(row[4]))
        for row in strategy_rows
    }
    recent_realized = [float(value or 0.0) for (value,) in curve_rows]
    running = float(agg[1]) - sum(recent_realized)
    curve = []
    for value in recent_realized:
        running += value
        curve.append(running)
    return PnlSummary(
        predicted_total=float(agg[0]), realized_total=float(agg[1]),
        model_error_total=float(agg[2]), session_predicted=float(agg[3]),
        session_realized=float(agg[4]), day_predicted=float(agg[5]),
        day_realized=float(agg[6]), measured_successes=int(agg[7]),
        execution_reverts=int(agg[8]), invalid_harness=int(agg[9]),
        infrastructure_errors=int(agg[10]),
        best_realized=(float(agg[11]) if agg[11] is not None else None),
        worst_realized=(float(agg[12]) if agg[12] is not None else None),
        realized_curve=tuple(curve), by_strategy=strategies,
    )


@dataclass(frozen=True)
class MonitoringSnapshot:
    now: float
    status: dict
    process: ProcessInfo | None
    process_alive: bool
    heartbeat_age_s: float | None
    cycle_age_s: float | None
    pnl: PnlSummary
    errors: tuple[str, ...] = ()
    log_tail: tuple[str, ...] = ()
    recent_forks: tuple[dict, ...] = ()
    recent_opportunities: tuple[dict, ...] = ()


def load_runtime_status(path: str) -> dict:
    try:
        with open(path) as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def tail_lines(path: str, n: int = 30) -> tuple[str, ...]:
    try:
        with open(path, errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return ()
    return tuple(line.rstrip("\n") for line in lines[-max(0, int(n)):])


def find_swarm_pid() -> int | None:
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "zero.cli swarm"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pids = [int(value) for value in out.split() if value.isdigit()]
    return max(pids) if pids else None


def read_process_info(pid: int) -> ProcessInfo | None:
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(int(pid)), "-o",
             "pid=,ppid=,%cpu=,%mem=,etime=,state=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        ).strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not out:
        return None
    try:
        return parse_ps_line(out.splitlines()[-1])
    except (TypeError, ValueError):
        return None


def _recent_forks(path: str, n: int = 8) -> tuple[dict, ...]:
    conn = _readonly_connection(path)
    if conn is None:
        return ()
    try:
        rows = conn.execute(
            "SELECT created_at, block, strategy, success, predicted_net, "
            "realized_net, model_error, COALESCE(outcome_class,'invalid_harness') "
            "FROM fork_verifications ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        conn.close()
    return tuple({
        "created_at": r[0], "block": r[1], "strategy": r[2],
        "success": bool(r[3]), "predicted_net": float(r[4] or 0.0),
        "realized_net": float(r[5] or 0.0), "model_error": float(r[6] or 0.0),
        "outcome_class": r[7],
    } for r in rows)


def _recent_opportunities(path: str, n: int = 8) -> tuple[dict, ...]:
    conn = _readonly_connection(path)
    if conn is None:
        return ()
    try:
        rows = conn.execute(
            "SELECT created_at, block, strategy, decision, asset, loan_size, "
            "net_profit, reason FROM opportunities "
            "WHERE decision != 'REJECT' ORDER BY id DESC LIMIT ?", (int(n),)).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        conn.close()
    return tuple({
        "created_at": r[0], "block": r[1], "strategy": r[2],
        "decision": r[3], "asset": r[4], "loan_size": r[5],
        "net_profit": r[6], "reason": r[7],
    } for r in rows)


def _is_swarm_process(process: ProcessInfo | None, *,
                      process_started_at: float | None = None,
                      now: float | None = None) -> bool:
    if process is None or not process.alive:
        return False
    if "zero.cli swarm" not in process.command.lower():
        return False
    if process_started_at is None or now is None:
        return True
    expected_age = max(0.0, float(now) - float(process_started_at))
    tolerance = max(30.0, min(300.0, expected_age * 0.10))
    return abs(float(process.elapsed_s) - expected_age) <= tolerance


def load_monitoring_snapshot(*, status_path: str, ledger_path: str,
                             log_path: str, now: float | None = None,
                             process_reader=read_process_info,
                             process_finder=find_swarm_pid) -> MonitoringSnapshot:
    now = float(time.time() if now is None else now)
    status = load_runtime_status(status_path)
    recorded_pid = status.get("process_pid")
    process = None
    if recorded_pid is not None:
        candidate = process_reader(int(recorded_pid))
        if _is_swarm_process(
                candidate, process_started_at=status.get("process_started_at"), now=now):
            process = candidate
    if process is None:
        fallback_pid = process_finder()
        if fallback_pid is not None:
            candidate = process_reader(int(fallback_pid))
            if _is_swarm_process(candidate):
                process = candidate
    heartbeat = status.get("heartbeat_at")
    cycle_started = status.get("cycle_started_at")
    heartbeat_age = (max(0.0, now - float(heartbeat)) if heartbeat else None)
    cycle_age = (max(0.0, now - float(cycle_started)) if cycle_started else None)
    process_start = status.get("process_started_at")
    pnl = load_pnl_summary(
        ledger_path,
        session_start=(float(process_start) if process_start else None),
        now=now,
    )
    log_tail = tail_lines(log_path, 40)
    markers = ("error", "exception", "traceback", "failed", "failure",
               "revert", "timeout", "429", "403", "stale", "forbidden")
    errors = tuple(line for line in log_tail if any(
        marker in line.lower() for marker in markers))[-12:]
    return MonitoringSnapshot(
        now=now,
        status=status,
        process=process,
        process_alive=bool(process and process.alive),
        heartbeat_age_s=heartbeat_age,
        cycle_age_s=cycle_age,
        pnl=pnl,
        errors=errors,
        log_tail=log_tail,
        recent_forks=_recent_forks(ledger_path),
        recent_opportunities=_recent_opportunities(ledger_path),
    )
