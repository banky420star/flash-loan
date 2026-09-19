#!/usr/bin/env python3
"""ZERO Engine live dashboard TUI (pure stdlib, read-only).

Run:  python3 scripts/tui.py
Refreshes every 10s: engine cycle status, hot wallet balance, BTC
stop-condition address, ledger near-miss stats.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "run", "swarm.log")
LEDGER = os.path.join(REPO, "zero_ledger.db")
HOT_WALLET = "0xc78096ce520d4d676e9b26dea1027bf32b390a2c"
BTC_ADDR = "39Ekn5B7ufkpPMQzmfzxjVwyYNMfP25f6a"
RPCS = ["https://arbitrum-one.public.blastapi.io", "https://arb1.arbitrum.io/rpc"]

CLEAR = "\033[2J\033[H"
BOLD, DIM, RST = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YEL, CYN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    for url in RPCS:
        try:
            req = urllib.request.Request(url, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "zero-tui/1.0"},
                                         data=body)
            with urllib.request.urlopen(req, timeout=10) as r:
                out = json.loads(r.read())
            if "result" in out:
                return out["result"]
        except Exception:
            continue
    return None


def engine_status():
    try:
        out = subprocess.run(["pgrep", "-f", "zero.cli swarm"],
                             capture_output=True, text=True).stdout.strip()
        if not out:
            return RED + "DEAD" + RST, None
        pid = out.splitlines()[0]
    except Exception:
        return RED + "?" + RST, None
    try:
        with open(LOG) as f:
            lines = f.readlines()[-120:]
        good = [l for l in lines if l.startswith("block")]
        errs = sum(1 for l in lines if "cycle error" in l)
        last = good[-1].strip() if good else None
        return GREEN + f"RUNNING pid {pid}" + RST, (last, errs)
    except Exception:
        return GREEN + f"RUNNING pid {pid}" + RST, None


def mainnet_eth():
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://ethereum-rpc.publicnode.com", method="POST",
                headers={"Content-Type": "application/json",
                         "User-Agent": "zero-tui/1.0"},
                data=json.dumps({"jsonrpc": "2.0", "id": 1,
                                 "method": "eth_getBalance",
                                 "params": [HOT_WALLET, "latest"]}).encode()),
                timeout=10) as r:
            return int(json.load(r)["result"], 16) / 1e18
    except Exception:
        return None


def wallet_eth():
    r = rpc("eth_getBalance", [HOT_WALLET, "latest"])
    try:
        return int(r, 16) / 1e18
    except Exception:
        return None


def btc_balance():
    try:
        with urllib.request.urlopen(
                f"https://blockchain.info/rawaddr/{BTC_ADDR}", timeout=15) as r:
            return json.load(r)["final_balance"] / 1e8
    except Exception:
        return None


def trader_status():
    """(status, last_event_summary) for the live trader."""
    try:
        out = subprocess.run(["pgrep", "-f", "scripts/live_trader.py"],
                             capture_output=True, text=True).stdout.strip()
        running = bool(out)
    except Exception:
        running = False
    last = None
    try:
        events = json.loads((os.path.join(REPO, "run", "live_trades.json"))
                            and open(os.path.join(REPO, "run",
                                                  "live_trades.json")).read())
        if events:
            ev = events[-1]
            last = ev.get("event", "?") + (f" tx {ev['tx'][:12]}…"
                                           if ev.get("tx") else "")
    except Exception:
        pass
    return running, last


def sniper_status():
    """(running, one-line summary) for the new-pool sniper."""
    try:
        out = subprocess.run(["pgrep", "-f", "scripts/newpool_sniper.py"],
                             capture_output=True, text=True).stdout.strip()
        running = bool(out)
    except Exception:
        running = False
    info = "no new pools seen"
    try:
        events = json.loads((os.path.join(REPO, "run", "newpool_events.json"))
                            and open(os.path.join(REPO, "run",
                                                  "newpool_events.json")).read())
        pools = [e for e in events if e.get("event") == "new_pool"]
        if pools:
            p = pools[-1]
            info = (f"{len(pools)} new pool(s) seen, latest "
                    f"{p.get('pair', '?')} fee {p.get('fee')}")
    except Exception:
        pass
    return running, info


def ledger_stats():
    try:
        con = sqlite3.connect(LEDGER)
        cur = con.cursor()
        total = cur.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
        row = cur.execute(
            "SELECT COUNT(*), MAX(net_profit) FROM opportunities "
            "WHERE ts > strftime('%s','now') - 10800").fetchone()
        con.close()
        return total, row[0], row[1]
    except Exception:
        return None, None, None


def render():
    now = time.strftime("%H:%M:%S")
    lines = []
    lines.append(f"{BOLD}  ZERO ENGINE — LIVE DASHBOARD{RST}  {DIM}{now}{RST}")
    lines.append("  " + "─" * 62)

    status, info = engine_status()
    lines.append(f"  Engine      {status}")
    if info:
        last, errs = info
        lines.append(f"  Last cycle  {last}")
        ec = "" if errs == 0 else f"  {YEL}{errs} errors in recent log{RST}"
        lines.append(f"{DIM}  (recent log){RST}{ec}")

    eth = wallet_eth()
    mn = mainnet_eth()
    if eth is None:
        lines.append(f"  Hot wallet  {RED}?{RST}")
    else:
        mark = GREEN if eth > 0 else RED
        note = ("LIVE — gas funded" if eth >= 0.0002
                else "DUST — top up for trading")
        lines.append(f"  Hot wallet  {eth:.5f} ETH  {mark}{note}{RST}")
        if mn:
            lines.append(f"  {DIM}mainnet  {mn:.6f} ETH  "
                         f"{'(in flight to Arbitrum)' if mn > 0 and eth == 0 else ''}{RST}")
    lines.append(f"  {DIM}{HOT_WALLET}{RST}")

    running, last_ev = trader_status()
    if running:
        lines.append(f"  Live trader {GREEN}ARMED{RST}"
                     + (f"  {DIM}last: {last_ev}{RST}" if last_ev else
                        f"  {DIM}awaiting first candidate{RST}"))
    else:
        lines.append(f"  Live trader {RED}NOT RUNNING{RST}"
                     f"  {DIM}(python3 scripts/live_trader.py){RST}")

    snip_running, snip_info = sniper_status()
    if snip_running:
        lines.append(f"  Newpool     {GREEN}WATCHING{RST}  {DIM}{snip_info}{RST}")
    else:
        lines.append(f"  Newpool     {RED}NOT RUNNING{RST}"
                     f"  {DIM}(python3 scripts/newpool_sniper.py){RST}")

    btc = btc_balance()
    if btc is None:
        lines.append(f"  Loop goal   {RED}?{RST}")
    else:
        mark = GREEN if btc >= 1.0 else YEL
        lines.append(f"  Loop goal   {mark}{btc:.8f} BTC / 1.0{RST}  "
                     f"{DIM}({BTC_ADDR}){RST}")

    total, recent, best = ledger_stats()
    lines.append("  " + "─" * 62)
    if total is not None:
        best_s = f"{best:.3f}" if best is not None else "—"
        lines.append(f"  Ledger      {total:,} rows | last 3h: {recent:,} evals,"
                     f" best net ${best_s}")
    else:
        lines.append("  Ledger      unavailable")
    lines.append("")
    lines.append(f"  {DIM}refresh 10s · Ctrl-C to exit{RST}")
    return "\n".join(lines)


def main():
    sys.stdout.write("\033[H\033[2J")
    while True:
        sys.stdout.write(CLEAR + render())
        sys.stdout.flush()
        time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass