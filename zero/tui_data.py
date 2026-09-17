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
    try:
        rows = conn.execute(
            "SELECT ts, strategy, success, predicted_net, realized_net, "
            "model_error, COALESCE(outcome_class,'invalid_harness') "
            "FROM fork_verifications ORDER BY id ASC").fetchall()
    except sqlite3.Error:
        conn.close()
        return PnlSummary()
    finally:
        if conn is not None:
            conn.close()

    predicted = realized = model_error = 0.0
    session_predicted = session_realized = 0.0
    successes = reverts = invalid = infra = 0
    by_strategy: dict[str, list[float]] = {}
    curve: list[float] = []
    running = 0.0
    realized_values: list[float] = []

    for ts, strategy, success, pred, real, error, outcome in rows:
        if outcome == "invalid_harness":
            invalid += 1
            continue
        if outcome == "infrastructure_error":
            infra += 1
            continue
        if outcome not in {"measured_success", "execution_revert"}:
            continue
        pred = float(pred or 0.0)
        real = float(real or 0.0)
        error = float(error or 0.0)
        predicted += pred
        realized += real
        model_error += error
        successes += int(outcome == "measured_success" and bool(success))
        reverts += int(outcome == "execution_revert")
        if session_start is not None and float(ts or 0.0) >= session_start:
            session_predicted += pred
            session_realized += real
        bucket = by_strategy.setdefault(str(strategy), [0.0, 0.0, 0.0, 0.0])
        bucket[0] += pred
        bucket[1] += real
        bucket[2] += error
        bucket[3] += 1
        running += real
        curve.append(running)
        realized_values.append(real)

    strategies = {
        name: StrategyPnl(values[0], values[1], values[2], int(values[3]))
        for name, values in by_strategy.items()
    }
    return PnlSummary(
        predicted_total=predicted,
        realized_total=realized,
        model_error_total=model_error,
        session_predicted=session_predicted,
        session_realized=session_realized,
        measured_successes=successes,
        execution_reverts=reverts,
        invalid_harness=invalid,
        infrastructure_errors=infra,
        best_realized=max(realized_values) if realized_values else None,
        worst_realized=min(realized_values) if realized_values else None,
        realized_curve=tuple(curve[-80:]),
        by_strategy=strategies,
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


def load_monitoring_snapshot(*, status_path: str, ledger_path: str,
                             log_path: str, now: float | None = None,
                             process_reader=read_process_info) -> MonitoringSnapshot:
    now = float(time.time() if now is None else now)
    status = load_runtime_status(status_path)
    pid = status.get("process_pid")
    process = process_reader(int(pid)) if pid is not None else None
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
