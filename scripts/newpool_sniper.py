#!/usr/bin/env python3
"""Event-driven new-pool sniper.

Watches the Uniswap V3 and Sushi V3 factories for PoolCreated events. When a
pool involving only executor-allowlisted tokens opens, it quotes the new pool
against a counterpart pool on either venue and, if the round trip nets more
than the live gate after the Aave premium and gas, fires the ZeroExecutor.

Why this can work when quote-polling doesn't: a fresh pool's initial price is
set by its deployer, and most indexers/aggregators learn about the pool only
after their next factory resync. The skew lives in that window, and it is
event-driven — no paid RPC needed to poll quotes we'd never win anyway.

Safety model (deliberately strict, per the thin-pool scam literature):
  * only pairs where BOTH tokens are executor-allowlisted blue chips — no
    unknown tokens, so no honeypot/tax-token risk and no new approvals;
  * one side of the pair must be a stable (price anchor + flash asset);
  * every quote runs our exact single-range pool math with an out-of-range
    guard; a fresh pool with no liquidity quotes 0 and is skipped.

Run from repo root:  python3 scripts/newpool_sniper.py
Events log to run/newpool_events.json.
"""
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from zero.keccak import keccak256, selector_hex
from zero.rpc import decode_uints, encode_address, encode_uint, Rpc
from zero.uniswap_v3 import UniswapV3Pool
from zero.live_executor import check_gate, GateDenied
import live_trader

CFG = json.loads((REPO / "config" / "arbitrum.json").read_text())
EVENTS_PATH = REPO / "run" / "newpool_events.json"
STATE_PATH = REPO / "run" / "newpool_state.json"

UNISWAP_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
SUSHI_FACTORY = "0x1af415a1EbA07a4986a52B6f2e7dE7003D82231e"
FACTORIES = [UNISWAP_FACTORY, SUSHI_FACTORY]

# Executor-allowlisted tokens = config/arbitrum.json "tokens" (the exact set
# the allowlist script put on-chain). Pairs must be entirely inside this set.
STABLE_SYMS = {"usdc", "usd₮0", "usdt0", "usdc.e", "dai"}
FEE_TIERS = [100, 500, 3000, 10000]      # getPool probes for counterparts
SIZES_USD = [50.0, 200.0, 1000.0]        # probe ladder, smallest first
AAVE_PREMIUM = 0.0005
GAS_COST_USD = 0.05                      # ~500k @ 0.02 gwei, ETH ~$2600
MIN_NET_USD = 5.0                        # matches config/live.json gate
POLL_SECONDS = 3


def log(event: dict) -> None:
    try:
        events = json.loads(EVENTS_PATH.read_text()) if EVENTS_PATH.exists() else []
    except Exception:
        events = []
    events.append(event)
    EVENTS_PATH.write_text(json.dumps(events[-1000:], indent=1))
    print(json.dumps(event), flush=True)


def ev_call(rpc, to, data, block="latest"):
    try:
        return rpc.call("eth_call", [{"to": to, "data": data}, block])
    except Exception:
        return None


def token_decimals(rpc, addr):
    raw = ev_call(rpc, addr, selector_hex("decimals()"))
    if not raw:
        return None
    try:
        data = bytes.fromhex(raw[2:]) if isinstance(raw, str) else raw
        return decode_uints(data)[0]
    except Exception:
        return None


def get_pool(rpc, factory, token_a, token_b, fee):
    data = selector_hex("getPool(address,address,uint24)") \
        + encode_address(token_a)[2:] + encode_address(token_b)[2:] \
        + encode_uint(fee)[2:]
    out = ev_call(rpc, factory, data)
    if not out:
        return None
    return None if int(out[-40:], 16) == 0 else "0x" + out[-40:]


def pool_state(rpc, pool):
    try:
        return UniswapV3Pool(rpc, pool, None, None, 0, 0, 0).fetch_state()
    except Exception:
        return None


def quote_on(rpc, pool, fee_percent, token0, token1, dec0, dec1,
             token_in, amount_in):
    """Exact single-range quote on one V3(-fork) pool. Returns out or None.

    amount_in is in BASE units (as the executor steps need it); our pool
    math takes HUMAN units, so rescale here.
    """
    state = pool_state(rpc, pool)
    if not state:
        return None
    direction = "0to1" if token_in == token0 else "1to0"
    dec_in, dec_out = (dec0, dec1) if direction == "0to1" else (dec1, dec0)
    res = UniswapV3Pool.quote(state, amount_in / 10 ** dec_in, direction,
                              dec_in, dec_out, fee_percent)
    return None if res["out_of_range"] else res["out"]


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(last_block: int) -> None:
    STATE_PATH.write_text(json.dumps({"last_block": last_block}))


def handle_pool(rpc, fallback, lg, tokens):
    topics = lg.get("topics", [])
    if len(topics) < 4:
        return
    token0 = "0x" + topics[1][-40:]
    token1 = "0x" + topics[2][-40:]
    fee = int(topics[3], 16)
    data = lg.get("data", "0x")
    pool = "0x" + data[-40:] if len(data) >= 66 else None
    t0, t1 = tokens.get(token0.lower()), tokens.get(token1.lower())
    if pool is None or t0 is None or t1 is None:
        log({"ts": time.time(), "event": "pool_ignored",
             "pool": pool, "token0": token0, "token1": token1,
             "reason": "non-allowlisted token"})
        return
    log({"ts": time.time(), "event": "new_pool", "pool": pool,
         "pair": f"{t0['sym']}/{t1['sym']}", "fee": fee})

    # Counterpart pool on either venue (same pair, any tier, not this pool).
    counterpart = None
    for fac in FACTORIES:
        for tier in FEE_TIERS:
            cp = get_pool(rpc, fac, token0, token1, tier)
            if cp and cp.lower() != pool.lower():
                st = pool_state(rpc, cp)
                if st and st["liquidity"] > 0:
                    counterpart = (cp, tier)
                    break
        if counterpart:
            break
    if not counterpart:
        log({"ts": time.time(), "event": "no_counterpart",
             "pool": pool, "pair": f"{t0['sym']}/{t1['sym']}"})
        return
    cp_addr, cp_fee = counterpart

    # Flash asset + price anchor: must be a stable on one side.
    stable_addr = token0.lower() if t0["sym"] in STABLE_SYMS else (
        token1.lower() if t1["sym"] in STABLE_SYMS else None)
    if stable_addr is None:
        log({"ts": time.time(), "event": "no_stable_anchor",
             "pool": pool, "pair": f"{t0['sym']}/{t1['sym']}"})
        return
    other_addr = token1.lower() if stable_addr == token0.lower() else token0.lower()
    stable, other = tokens[stable_addr], tokens[other_addr]

    best = None
    for size_usd in SIZES_USD:
        amount_in = int(round(size_usd * 10 ** stable["dec"]))
        # hop1: stable -> other on the NEW pool (base units in, human out)
        h1 = quote_on(rpc, pool, fee / 1e4, token0, token1,
                      t0["dec"], t1["dec"], stable_addr, amount_in)
        if h1 is None or h1 <= 0:
            continue
        # hop2: other -> stable on the counterpart pool, input shrunk by the
        # same buffer live_trader.build_steps applies (keeps expected outputs
        # consistent with the on-chain step amounts and minOut floors)
        h2_in = int(round(h1 * (1 - live_trader.SWAP_BUFFER) * 10 ** other["dec"]))
        h2 = quote_on(rpc, cp_addr, cp_fee / 1e4, token0, token1,
                      t0["dec"], t1["dec"], other_addr, h2_in)
        if h2 is None or h2 <= 0:
            continue
        # quote outs are already HUMAN units; net in USD directly
        net_usd = (h2 - size_usd) - size_usd * AAVE_PREMIUM - GAS_COST_USD
        row = {"size_usd": size_usd, "net_usd": net_usd,
               "hop1_out": h1, "hop2_out": h2}
        if best is None or net_usd > best["net_usd"]:
            best = row
    if best is None:
        log({"ts": time.time(), "event": "unquotable", "pool": pool})
        return
    log({"ts": time.time(), "event": "evaluated", "pool": pool,
         "pair": f"{t0['sym']}/{t1['sym']}", "counterpart": cp_addr,
         "best": best})

    if best["net_usd"] < MIN_NET_USD:
        return

    stable_is_t0 = stable_addr == token0.lower()
    # Implied mid price in USD (stable side anchors at $1) — live_trader.fire
    # refuses to fire without an asset USD price.
    mid_price = best["size_usd"] / best["hop1_out"] if best["hop1_out"] else 0.0
    cand = {
        "id": f"newpool:{pool}",
        "block": int(lg.get("blockNumber", "0x0"), 16),
        "asset": stable_addr,
        "loan_size": best["size_usd"],          # human units
        "hop1_out": best["hop1_out"],           # human units (mid token)
        "hop2_out": best["hop2_out"],           # human units (asset)
        "expected_net_usd": best["net_usd"],
        "name": f"newpool {stable['sym']}->{other['sym']}->{stable['sym']}",
        "route": {
            "base": stable_addr if stable_is_t0 else other_addr,
            "quote": other_addr if stable_is_t0 else stable_addr,
            "base_decimals": tokens[(stable_addr if stable_is_t0
                                     else other_addr)]["dec"],
            "quote_decimals": tokens[(other_addr if stable_is_t0
                                      else stable_addr)]["dec"],
            "base_price_usd": 1.0 if stable_is_t0 else mid_price,
            "quote_price_usd": mid_price if stable_is_t0 else 1.0,
            "pools": [
                {"address": pool, "fee_tier": fee},
                {"address": cp_addr, "fee_tier": cp_fee},
            ],
        },
    }
    try:
        check_gate(cand)
    except GateDenied as exc:
        log({"ts": time.time(), "event": "gate_denied", "pool": pool,
             "reason": str(exc)})
        return
    live_trader.fire(rpc, cand, None)
    time.sleep(30)                # cooldown after a fired attempt


LOGS_PRIMARY = "https://arb1.arbitrum.io/rpc"      # allows big getLogs ranges
LOGS_FALLBACK = "https://arbitrum-one.public.blastapi.io"  # 10-block cap
BLAST_CHUNK = 10


def fetch_new_pool_logs(rpc, fallback, frm, head, topic0):
    """getLogs with big ranges on the official RPC; 10-block chunks on
    BlastAPI (its free tier caps eth_getLogs ranges)."""
    try:
        return Rpc(LOGS_PRIMARY).call("eth_getLogs", [{
            "fromBlock": hex(frm), "toBlock": hex(head), "topics": [topic0]}])
    except Exception:
        pass
    out = []
    cur = frm
    while cur <= head:
        hi = min(cur + BLAST_CHUNK - 1, head)
        out.extend(Rpc(LOGS_FALLBACK).call("eth_getLogs", [{
            "fromBlock": hex(cur), "toBlock": hex(hi), "topics": [topic0]}]))
        cur = hi + 1
    return out


def main() -> None:
    rpc = Rpc("https://arbitrum-one.public.blastapi.io")
    fallback = Rpc("https://arb1.arbitrum.io/rpc")

    # Allowlisted tokens from config, decimals resolved live per address.
    tokens = {}
    for sym, addr in CFG["tokens"].items():
        dec = token_decimals(rpc, addr)
        if dec is None:
            log({"ts": time.time(), "event": "token_skip",
                 "sym": sym, "addr": addr, "reason": "no decimals"})
            continue
        tokens[addr.lower()] = {"sym": sym.lower(), "dec": dec}
    log({"ts": time.time(), "event": "start",
         "tokens": sorted(t["sym"] for t in tokens.values())})

    topic0 = "0x" + keccak256(
        b"PoolCreated(address,address,uint24,int24,address)").hex()

    last_block = int(load_state().get("last_block", 0))
    head = int(rpc.call("eth_blockNumber", []), 16)
    if last_block == 0:
        last_block = head - 200       # small warmup window; skip the backlog

    # Diagnostic: confirm both factories actually emit this event signature.
    for name, fac in (("uniswap", UNISWAP_FACTORY), ("sushi", SUSHI_FACTORY)):
        try:
            probe = Rpc(LOGS_PRIMARY).call("eth_getLogs", [{
                "fromBlock": hex(head - 350000), "toBlock": hex(head),
                "address": fac, "topics": [topic0]}])
            log({"ts": time.time(), "event": "factory_probe", "venue": name,
                 "matches_1d": len(probe or [])})
        except Exception as exc:
            log({"ts": time.time(), "event": "factory_probe_error",
                 "venue": name, "error": str(exc)[:120]})

    while True:
        try:
            head = int(rpc.call("eth_blockNumber", []), 16)
            if head <= last_block:
                time.sleep(POLL_SECONDS)
                continue
            frm = last_block + 1
            logs = None
            try:
                logs = fetch_new_pool_logs(rpc, fallback, frm, head, topic0)
            except Exception as exc:
                log({"ts": time.time(), "event": "getlogs_error",
                     "error": str(exc)[:150], "from": frm, "to": head})
            for lg in logs or []:
                try:
                    handle_pool(rpc, fallback, lg, tokens)
                except Exception as exc:
                    log({"ts": time.time(), "event": "handle_error",
                         "error": str(exc)[:200]})
            last_block = head
            save_state(last_block)
        except Exception as exc:
            log({"ts": time.time(), "event": "loop_error",
                 "error": str(exc)[:200]})
            time.sleep(POLL_SECONDS * 2)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()