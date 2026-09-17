from __future__ import annotations

import argparse
import curses
import os
import time

from .tui_data import MonitoringSnapshot, load_monitoring_snapshot


def _money(value: float | None) -> str:
    return "--" if value is None else f"${float(value):+,.4f}"


def _duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "--"
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _rule(title: str, width: int) -> str:
    label = f" {title} "
    return (label + "-" * max(0, width - len(label)))[:width]


def _sparkline(values, width: int = 36) -> str:
    values = list(values)[-max(1, width):]
    if not values:
        return "(no measured fork P&L yet)"
    chars = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi == lo:
        return chars[0] * len(values)
    return "".join(chars[min(7, int((v - lo) / (hi - lo) * 7))] for v in values)


def _strategy_name(name: str) -> str:
    return {"swarm_arbitrage": "Arbitrage",
            "swarm_liquidation": "Liquidation"}.get(name, name)


def render_control(snapshot: MonitoringSnapshot, *, width: int = 120) -> str:
    width = max(72, int(width))
    s = snapshot.status
    p = snapshot.pnl
    proc = "PROCESS ALIVE" if snapshot.process_alive else "PROCESS NOT FOUND"
    phase = str(s.get("cycle_phase", "unknown"))
    block = s.get("block", "--")
    head = s.get("chain_head", "--")
    lag = s.get("block_lag", "--")
    lines = [
        _rule("ZERO CONTROL FLOOR", width),
        f"{proc} | Cycle {s.get('cycle_number', '--')} {phase} | "
        f"Heartbeat {snapshot.heartbeat_age_s if snapshot.heartbeat_age_s is not None else '--'}s | "
        f"Cycle age {_duration(snapshot.cycle_age_s)}",
        f"Block {block} | Head {head} | Lag {lag} | RPC {s.get('rpc_endpoint') or '--'}",
        _rule("SWARM", width),
        f"Workers {s.get('active_workers', 0)}/20 | Routes {s.get('routes_scanned', 0)} | "
        f"Worker failures {s.get('worker_failures', 0)} | Positive {s.get('positive_net', 0)}",
        f"Fork attempts {s.get('fork_attempted', 0)} | Passed {s.get('fork_passed', 0)} | "
        f"Failed {s.get('fork_failed', 0)} | Last cycle {float(s.get('elapsed_s', 0) or 0):.2f}s",
        _rule("FORK / SHADOW P&L - SIMULATED, NOT WALLET P&L", width),
        f"Session  predicted {_money(p.session_predicted):>12}  realized {_money(p.session_realized):>12}",
        f"Today    predicted {_money(p.day_predicted):>12}  Today realized {_money(p.day_realized):>12}",
        f"All-time predicted {_money(p.predicted_total):>12}  realized {_money(p.realized_total):>12}  "
        f"model error {_money(p.model_error_total):>12}",
        f"Measured success {p.measured_successes} | execution reverts {p.execution_reverts} | "
        f"invalid harness {p.invalid_harness} | infrastructure {p.infrastructure_errors}",
        f"Best measured {_money(p.best_realized)} | Worst measured {_money(p.worst_realized)}",
        "Cumulative simulated realized: " + _sparkline(p.realized_curve, min(50, width - 32)),
    ]
    for name in sorted(p.by_strategy):
        item = p.by_strategy[name]
        lines.append(
            f"{_strategy_name(name):12s} predicted {_money(item.predicted):>12}  "
            f"realized {_money(item.realized):>12}  n={item.count}")
    lines.append(_rule("RECENT FORK RESULTS", width))
    if snapshot.recent_forks:
        for row in snapshot.recent_forks[:6]:
            lines.append(
                f"b{row.get('block','--')} {_strategy_name(str(row.get('strategy',''))):12s} "
                f"pred {_money(row.get('predicted_net')):>11} real {_money(row.get('realized_net')):>11} "
                f"err {_money(row.get('model_error')):>11} {row.get('outcome_class','--')}")
    else:
        lines.append("No fork verification records yet.")
    lines.append(_rule("ERRORS / EVENTS", width))
    if snapshot.errors:
        lines.extend(snapshot.errors[-6:])
    else:
        lines.append("No recent matched errors in run/swarm.log.")
    lines.append(_rule("KEYS: 1 Control | 2 Process | p Pause | r Refresh | q Quit", width))
    return "\n".join(line[:width] for line in lines)


def render_process(snapshot: MonitoringSnapshot, *, width: int = 120) -> str:
    width = max(72, int(width))
    s = snapshot.status
    proc = snapshot.process
    lines = [_rule("ZERO PROCESS MONITOR", width)]
    if proc is None:
        lines.append("PROCESS NOT FOUND | status PID unavailable or no matching swarm process")
    else:
        lines.append(
            f"PROCESS ALIVE | PID {proc.pid} | CPU {proc.cpu_percent:.1f}% | "
            f"RAM {proc.memory_percent:.1f}% | UP {_duration(proc.elapsed_s)} | STATE {proc.state}")
        lines.append("Command: " + proc.command)
    lines.extend([
        _rule("RUNTIME", width),
        f"Cycle phase {s.get('cycle_phase','unknown')} | Cycle #{s.get('cycle_number','--')} | "
        f"Cycle age {_duration(snapshot.cycle_age_s)}",
        f"Heartbeat age {snapshot.heartbeat_age_s if snapshot.heartbeat_age_s is not None else '--'}s | "
        f"Last cycle {float(s.get('elapsed_s', 0) or 0):.2f}s | Failures {s.get('worker_failures', 0)}",
        f"Block {s.get('block','--')} | Head {s.get('chain_head','--')} | Lag {s.get('block_lag','--')} | "
        f"RPC {s.get('rpc_endpoint') or '--'}",
        _rule("P&L TELEMETRY", width),
        f"Session simulated realized {_money(snapshot.pnl.session_realized)} | "
        f"Today {_money(snapshot.pnl.day_realized)} | All-time {_money(snapshot.pnl.realized_total)}",
        f"Harness errors {snapshot.pnl.invalid_harness} | Infrastructure errors {snapshot.pnl.infrastructure_errors} | "
        f"Execution reverts {snapshot.pnl.execution_reverts}",
        _rule("ERROR STREAM", width),
    ])
    lines.extend(snapshot.errors[-10:] if snapshot.errors else ("No recent matched errors.",))
    lines.append(_rule("LOG TAIL", width))
    lines.extend(snapshot.log_tail[-8:] if snapshot.log_tail else ("No log output yet.",))
    lines.append(_rule("KEYS: 1 Control | 2 Process | p Pause | r Refresh | q Quit", width))
    return "\n".join(line[:width] for line in lines)


def _snapshot(args) -> MonitoringSnapshot:
    return load_monitoring_snapshot(
        status_path=args.status,
        ledger_path=args.ledger,
        log_path=args.log,
    )


def _draw(stdscr, text: str) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    for row, line in enumerate(text.splitlines()[:max(0, height - 1)]):
        try:
            stdscr.addnstr(row, 0, line, max(1, width - 1))
        except curses.error:
            pass
    stdscr.refresh()


def _curses_loop(stdscr, args) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.nodelay(False)
    stdscr.timeout(max(100, int(args.refresh * 1000)))
    mode = args.mode
    paused = False
    snapshot = None
    while True:
        if snapshot is None or not paused:
            snapshot = _snapshot(args)
        _, width = stdscr.getmaxyx()
        text = (render_control(snapshot, width=width)
                if mode == "control" else render_process(snapshot, width=width))
        if paused:
            text += "\n[DISPLAY PAUSED - engine continues running]"
        _draw(stdscr, text)
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key == ord("1"):
            mode = "control"
        elif key == ord("2"):
            mode = "process"
        elif key in (ord("p"), ord("P")):
            paused = not paused
        elif key in (ord("r"), ord("R")):
            snapshot = _snapshot(args)


def _default_paths():
    return (
        os.environ.get("ZERO_RUNTIME_STATUS_PATH", "run/zero-status.json"),
        os.environ.get("ZERO_LEDGER_PATH", "zero_ledger.db"),
        os.environ.get("ZERO_LOG_PATH", "run/swarm.log"),
    )


def main(argv=None) -> int:
    status_default, ledger_default, log_default = _default_paths()
    parser = argparse.ArgumentParser(prog="zero-tui")
    parser.add_argument("mode", choices=("control", "process"), nargs="?",
                        default="control")
    parser.add_argument("--status", default=status_default)
    parser.add_argument("--ledger", default=ledger_default)
    parser.add_argument("--log", default=log_default)
    parser.add_argument("--refresh", type=float, default=1.0)
    parser.add_argument("--once", action="store_true",
                        help="print one snapshot instead of opening curses")
    args = parser.parse_args(argv)
    if args.once:
        snap = _snapshot(args)
        text = (render_control(snap) if args.mode == "control"
                else render_process(snap))
        print(text)
        return 0
    curses.wrapper(_curses_loop, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
