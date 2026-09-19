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
import threading
import time
import urllib.request
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zero.keccak import keccak256

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "run", "swarm.log")
LEDGER = os.path.join(REPO, "zero_ledger.db")
HOT_WALLET = "0xc78096ce520d4d676e9b26dea1027bf32b390a2c"
BTC_ADDR = "39Ekn5B7ufkpPMQzmfzxjVwyYNMfP25f6a"
RPCS = ["https://arbitrum-one.public.blastapi.io", "https://arb1.arbitrum.io/rpc"]

CLEAR = "\033[2J\033[H"
BOLD, DIM, RST = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YEL, CYN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

CFG = json.loads(os.path.join(REPO, "config", "arbitrum.json")
                 and open(os.path.join(REPO, "config", "arbitrum.json")).read())
PRICE_BASE = "USDC"
# Price board: token vs USDC on each venue. Pool addresses resolved once at
# startup (deepest fee tier by liquidity), prices sampled from slot0.
PRICE_VENUES = [
    ("uni", CFG["venues"]["uniswap_v3"]["factory"]),
    ("sushi", CFG["venues"]["sushi_v3"]["factory"]),
    ("camelot", CFG["venues"]["camelot_v3"]["factory"]),
]
PRICE_TOKENS = ["WETH", "DAI", "USDt0", "USDC.e"]
PRICE_REFRESH_S = 30
_price_pools = {}          # token_sym -> {venue: {pool, token0, dec_tok}}
_price_board = {"ts": 0, "rows": []}


def _sel(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()[:8]


def _uint(hexdata):
    return int.from_bytes(bytes.fromhex(hexdata[2:]), "big")


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
                f"https://mempool.space/api/address/{BTC_ADDR}", timeout=15) as r:
            s = json.load(r)["chain_stats"]
            return (s["funded_txo_sum"] - s["spent_txo_sum"]) / 1e8
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


def recent_ledger(n=3):
    """Last n evaluated opportunities: [(time, strategy, size, net, decision)]."""
    try:
        con = sqlite3.connect(LEDGER)
        rows = con.execute(
            "SELECT ts, strategy, loan_size, net_profit, decision "
            "FROM opportunities ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        con.close()
        return rows
    except Exception:
        return []


def resolve_price_pools():
    """Once: find each token's deepest pool vs USDC on each venue."""
    usdc = CFG["tokens"]["USDC"]
    for sym in PRICE_TOKENS:
        tok = CFG["tokens"].get(sym)
        if not tok:
            continue
        dec = None
        try:
            dec = _uint(rpc("eth_call",
                            [{"to": tok, "data": _sel("decimals()")},
                             "latest"]))
        except Exception:
            continue
        venues = {}
        for vname, factory in PRICE_VENUES:
            pools = []
            if vname == "camelot":            # Algebra: getPool(a,b)
                sig = "getPool(address,address)"
                words = [tok[2:].lower().rjust(64, "0"),
                         usdc[2:].lower().rjust(64, "0")]
            else:                             # Uniswap V3: getPool(a,b,fee)
                sig = "getPool(address,address,uint24)"
                words = None
            try:
                if words is None:
                    for fee in (500, 3000, 100, 10000):
                        data = _sel(sig) + tok[2:].lower().rjust(64, "0") \
                            + usdc[2:].lower().rjust(64, "0") \
                            + f"{fee:064x}"
                        out = rpc("eth_call", [{"to": factory, "data": data},
                                               "latest"])
                        if out and int(out[-40:], 16) != 0:
                            pools.append("0x" + out[-40:])
                else:
                    out = rpc("eth_call", [{"to": factory,
                                            "data": _sel(sig) + "".join(words)},
                                           "latest"])
                    if out and int(out[-40:], 16) != 0:
                        pools.append("0x" + out[-40:])
            except Exception:
                continue
            # deepest = most liquidity
            best, best_liq = None, -1
            for pool in pools:
                try:
                    t0 = "0x" + rpc("eth_call",
                                    [{"to": pool, "data": _sel("token0()")},
                                     "latest"])[-40:]
                    liq = _uint(rpc("eth_call",
                                    [{"to": pool, "data": _sel("liquidity()")},
                                     "latest"]))
                    if liq > best_liq:
                        best, best_liq = {"pool": pool, "token0": t0.lower(),
                                          "dec_tok": dec}, liq
                except Exception:
                    continue
            if best:
                venues[vname] = best
        if venues:
            _price_pools[sym] = venues


def refresh_price_board():
    """Sample slot0 on every resolved pool; compute USD price per venue."""
    usdc = CFG["tokens"]["USDC"]
    rows = []
    for sym, venues in _price_pools.items():
        row = {"sym": sym, "prices": {}, "spread": None}
        for vname, meta in venues.items():
            try:
                data = rpc("eth_call",
                           [{"to": meta["pool"], "data": _sel("slot0()")},
                            "latest"])
                if not data:
                    continue
                sq = int(data[2:66], 16)         # slot0 word0 = sqrtPriceX96
                if sq <= 0:
                    continue
                # raw = token1_units per token0_units (exact rational)
                raw = Fraction(sq * sq, 1 << 192)
                tok_is0 = meta["token0"] == CFG["tokens"][sym].lower()
                # USD per whole token, USDC treated as $1
                dec_tok, dec_usdc = meta["dec_tok"], 6
                if tok_is0:                      # token=token0, USDC=token1
                    price = float(raw * 10 ** (dec_tok - dec_usdc))
                else:                            # USDC=token0
                    price = float(10 ** (dec_tok - dec_usdc) / raw)
                if price > 0:
                    row["prices"][vname] = price
            except Exception:
                continue
        if len(row["prices"]) >= 2:
            ps = [p for p in row["prices"].values() if p > 0]
            if ps:
                row["spread"] = (max(ps) - min(ps)) / min(ps) * 100
        rows.append(row)
    _price_board["rows"] = rows
    _price_board["ts"] = time.time()


_price_thread = None


def _price_worker():
    try:
        if not _price_pools:
            resolve_price_pools()
        if _price_pools:
            refresh_price_board()
    except Exception:
        pass


def price_panel_lines():
    global _price_thread
    if time.time() - _price_board["ts"] > PRICE_REFRESH_S \
            and (_price_thread is None or not _price_thread.is_alive()):
        _price_thread = threading.Thread(target=_price_worker, daemon=True)
        _price_thread.start()
    lines = []
    if not _price_board["rows"]:
        return [f"  {DIM}price board warming up…{RST}"]
    for row in _price_board["rows"]:
        parts = []
        for vname in ("uni", "sushi", "camelot"):
            p = row["prices"].get(vname)
            parts.append(f"{vname} {p:,.4f}" if p is not None
                         else f"{vname} {DIM}—{RST}")
        sp = row["spread"]
        sp_s = "—"
        if sp is not None:
            sp_s = f"{sp:.3f}%"
            if sp > 0.3:
                sp_s = YEL + sp_s + RST
        lines.append(f"  {row['sym']:6s} " + " | ".join(parts)
                     + f"   spread {sp_s}")
    return lines


def activity_feed(n=6, max_age=3600):
    """Merged recent events from the live trader and the new-pool sniper."""
    cutoff = time.time() - max_age
    events = []
    for name, path in (("trader", "run/live_trades.json"),
                       ("sniper", "run/newpool_events.json")):
        try:
            for ev in json.loads(open(os.path.join(REPO, path)).read()):
                if ev.get("ts", 0) >= cutoff:
                    events.append((ev.get("ts", 0), name, ev))
        except Exception:
            continue
    events.sort(reverse=True)
    out = []
    for ts, name, ev in events[:n]:
        t = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "--:--:--"
        kind = ev.get("event", "?")
        detail = ev.get("error") or ev.get("reason") or ev.get("tx") or \
            ev.get("pair") or ev.get("name") or ""
        if kind == "evaluated" and isinstance(ev.get("best"), dict):
            detail = f"{ev.get('pair','?')} best net ${ev['best'].get('net_usd', 0):.2f}"
        out.append(f"{t} {name:6s} {kind:14s} {str(detail)[:40]}")
    return out


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

    lines.append(f"  {BOLD}Prices vs USDC{RST}"
                 f"  {DIM}sampled {time.strftime('%H:%M:%S', time.localtime(_price_board['ts'])) if _price_board['ts'] else '…'}{RST}")
    lines.extend(price_panel_lines())

    lines.append(f"  {BOLD}Latest evaluations{RST}")
    for ts, strat, size, net, dec in recent_ledger():
        t = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "--:--:--"
        mark = GREEN if dec == "PASS" else (YEL if dec == "OBSERVE" else DIM)
        strat_s = (strat or "")[:16]
        lines.append(f"  {DIM}{t}{RST} {strat_s:16s} ${size:>10.2f}"
                     f"  net {net:>9.3f}  {mark}{dec}{RST}")

    feed = activity_feed()
    if feed:
        lines.append(f"  {BOLD}Live activity (trader + sniper){RST}")
        lines.extend(f"  {row}" for row in feed)

    lines.append("")
    lines.append(f"  {DIM}refresh 5s · Ctrl-C to exit{RST}")
    return "\n".join(lines)


def main():
    sys.stdout.write("\033[H\033[2J")
    while True:
        sys.stdout.write(CLEAR + render())
        sys.stdout.flush()
        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass