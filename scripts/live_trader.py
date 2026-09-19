#!/usr/bin/env python3
"""Live trader: watches the shadow ledger for gate-passing candidates and
fires them at the on-chain ZeroExecutor.

Pipeline per candidate:
  ledger (decision='PASS', new id) -> live gate (config/live.json) ->
  route -> Step[] (pool factory classification; Algebra routes skipped) ->
  eth_estimateGas -> sign -> broadcast -> receipt -> gas-spend accounting.

Fires at most one attempt per COOLDOWN seconds and only on fresh quotes
(candidate block within BLOCK_STALENESS of head). Run from repo root:
  python3 scripts/live_trader.py
Stop: touch run/KILL  (the gate refuses while it exists).
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zero.keccak import keccak256
from zero.live_executor import (check_gate, build_run_calldata,
                                record_gas_spend, GateDenied)
from zero.rpc import Rpc
from zero.wallet import sign_transaction, send_raw_transaction, \
    private_key_to_address

EXECUTOR = "0x78de834b65d26d35993e0c7ce5f2fc1ca7e461c9"
LEDGER = Path(__file__).resolve().parent.parent / "zero_ledger.db"
STATE_PATH = Path(__file__).resolve().parent.parent / "run" / "live_trades.json"
VENUE_CACHE_PATH = (Path(__file__).resolve().parent.parent / "run" /
                    "live_pool_venues.json")
HOT_WALLET = "0xc78096ce520d4d676e9b26dea1027bf32b390a2c"
POLL_SECONDS = 5
COOLDOWN = 60
BLOCK_STALENESS = 40          # Arbitrum ~250ms blocks; quotes die in seconds
SWAP_BUFFER = 0.01            # hop2 input shrinks 1% vs quoted hop1 output
MINPROFIT_FRACTION = 0.8      # executor floor at 80% of expected net

UNISWAP_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
SUSHI_FACTORY = "0x1af415a1EbA07a4986a52B6f2e7dE7003D82231e"
UNISWAP_ROUTER = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
SUSHI_ROUTER = "0x8A21F6768C1f8075791D08546Dadf6daA0bE820c"
GAS_PRICE_USD_ETH = 2638.0


def log(event: dict) -> None:
    events = []
    if STATE_PATH.exists():
        try:
            events = json.loads(STATE_PATH.read_text())
        except Exception:
            events = []
    events.append(event)
    STATE_PATH.write_text(json.dumps(events[-500:], indent=1))
    print(json.dumps(event), flush=True)


def pool_factory(rpc: Rpc, pool: str, cache: dict) -> str | None:
    """Classify a V3 pool by its factory. None = unknown/unsupported."""
    if pool in cache:
        return cache[pool]
    try:
        out = rpc.call("eth_call",
                       [{"to": pool,
                         "data": "0x" + keccak256(b"factory()").hex()[:8]},
                        "latest"])
        fac = "0x" + out[-40:]
    except Exception:
        fac = None
    cache[pool] = fac
    try:
        VENUE_CACHE_PATH.write_text(json.dumps({"pools": cache}, indent=1))
    except Exception:
        pass
    return fac


def factory_router(fac: str | None) -> str | None:
    if fac and fac.lower() == UNISWAP_FACTORY.lower():
        return UNISWAP_ROUTER
    if fac and fac.lower() == SUSHI_FACTORY.lower():
        return SUSHI_ROUTER
    return None


def build_steps(rpc: Rpc, cand: dict, cache: dict) -> list[dict] | None:
    """Route detail -> executor steps. None if any hop is unsupported."""
    route = cand["route"]
    pools = route["pools"]
    if len(pools) != 2:
        return None
    asset = cand["asset"].lower()
    base = route["base"].lower()
    quote = route["quote"].lower()
    if asset not in (base, quote):
        return None
    mid = quote if asset == base else base

    h1, h2 = pools
    r1 = factory_router(pool_factory(rpc, h1["address"], cache))
    r2 = factory_router(pool_factory(rpc, h2["address"], cache))
    if r1 is None or r2 is None:
        return None

    # loan_size / hop outputs are human units; convert with token decimals
    asset_dec = (route["base_decimals"] if asset == base
                 else route["quote_decimals"])
    mid_dec = (route["quote_decimals"] if asset == base
               else route["base_decimals"])
    hop1_out = cand.get("hop1_out")
    hop2_out = cand.get("hop2_out")
    if not hop1_out or not hop2_out:
        return None
    amount = int(round(float(cand["loan_size"]) * 10 ** asset_dec))
    hop1_out_i = int(round(float(hop1_out) * 10 ** mid_dec))
    hop2_in = int(hop1_out_i * (1 - SWAP_BUFFER))
    hop2_out_i = int(round(float(hop2_out) * 10 ** asset_dec))
    # hop2 runs on a slightly smaller input; scale its expected output down
    # and give the slippage guard a little extra room on top.
    limit1 = int(hop1_out_i * (1 - SWAP_BUFFER) * 0.999)
    limit2 = int(hop2_out_i * (1 - SWAP_BUFFER) * 0.999)

    return [
        {"kind": 0, "target": r1, "tokenIn": asset, "tokenOut": mid,
         "account": HOT_WALLET, "amount": amount, "limit": limit1,
         "fee": int(h1["fee_tier"]), "recipientMode": 1},
        {"kind": 0, "target": r2, "tokenIn": mid, "tokenOut": asset,
         "account": HOT_WALLET, "amount": hop2_in, "limit": limit2,
         "fee": int(h2["fee_tier"]), "recipientMode": 1},
    ]


def fire(rpc: Rpc, cand: dict, cfg: dict) -> None:
    key = bytes.fromhex((Path(__file__).resolve().parent.parent
                         / "wallet" / "hot.key").read_text().strip())
    steps = build_steps(rpc, cand, VENUE_CACHE_PATH.exists()
                        and json.loads(VENUE_CACHE_PATH.read_text()).get("pools", {}) or {})
    asset = cand["asset"].lower()
    route = cand["route"]
    amount = int(round(float(cand["loan_size"]) *
                       10 ** (route["base_decimals"] if asset == route["base"].lower()
                              else route["quote_decimals"])))
    price = float(route.get(
        "base_price_usd" if asset == route["base"].lower()
        else "quote_price_usd", 0))
    if price <= 0:
        return
    min_profit = max(1, int(float(cand["expected_net_usd"]) * 0.8 / price *
                            10 ** (route["base_decimals"] if asset == route["base"].lower()
                                   else route["quote_decimals"])))
    calldata = build_run_calldata("aave", asset, amount, min_profit,
                                  int(time.time()) + 90, steps)
    try:
        est = int(rpc.call("eth_estimateGas",
                           [{"from": HOT_WALLET, "to": EXECUTOR,
                             "data": "0x" + calldata.hex()}]), 16)
    except Exception as exc:
        log({"ts": time.time(), "id": cand["id"], "event": "estimate_failed",
             "error": str(exc)[:200]})
        return
    nonce = int(rpc.call("eth_getTransactionCount", [HOT_WALLET, "pending"]), 16)
    gp = int(rpc.call("eth_gasPrice", []), 16) / 1e9 * 2
    raw = sign_transaction(key, nonce=nonce, gas_price_gwei=gp,
                           gas_limit=int(est * 1.3) + 1, to=EXECUTOR,
                           value_wei=0, data=calldata, chain_id=42161)
    tx = send_raw_transaction(rpc, raw)
    gas_eth = est * 1.3 * gp * 1e-9
    log({"ts": time.time(), "id": cand["id"], "event": "fired",
         "tx": tx, "asset": asset, "amount": amount,
         "min_profit_units": min_profit, "gas_est": est,
         "route": cand.get("name")})
    record_gas_spend(gas_eth * GAS_PRICE_USD_ETH * 1.3)
    for _ in range(90):
        time.sleep(2)
        rec = rpc.call("eth_getTransactionReceipt", [tx])
        if rec:
            log({"ts": time.time(), "id": cand["id"], "event": "receipt",
                 "tx": tx, "status": rec["status"],
                 "gas_used": int(rec["gasUsed"], 16)})
            break


def main() -> None:
    rpc = Rpc("https://arbitrum-one.public.blastapi.io")
    con = sqlite3.connect(LEDGER)
    con.row_factory = sqlite3.Row
    last_id = con.execute("SELECT COALESCE(MAX(id),0) FROM opportunities"
                          ).fetchone()[0]
    print(f"live trader watching ledger from id {last_id}", flush=True)
    while True:
        time.sleep(POLL_SECONDS)
        rows = con.execute(
            "SELECT id, ts, asset, loan_size, min_profit, net_profit, detail "
            "FROM opportunities WHERE id > ? AND decision='PASS' "
            "ORDER BY id", (last_id,)).fetchall()
        for row in rows:
            last_id = row["id"]
            cand = json.loads(row["detail"])
            cand["asset"] = row["asset"]
            cand["loan_size"] = row["loan_size"]
            cand["id"] = row["id"]
            head = int(rpc.call("eth_blockNumber", []), 16)
            if head - int(cand.get("block", 0)) > BLOCK_STALENESS:
                log({"ts": time.time(), "id": row["id"],
                     "event": "stale_quote",
                     "age_blocks": head - int(cand.get("block", 0))})
                continue
            try:
                check_gate(cand)
            except GateDenied as exc:
                log({"ts": time.time(), "id": row["id"],
                     "event": "gate_denied", "reason": str(exc)})
                continue
            fire(rpc, cand, None)
            time.sleep(COOLDOWN)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass