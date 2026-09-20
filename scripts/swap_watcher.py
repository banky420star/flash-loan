#!/usr/bin/env python3
"""Event-driven dislocation watcher: react to Swap events on executor-
allowlisted V3 pools and fire the ZeroExecutor when two pools of the same
pair momentarily disagree by more than the round-trip cost.

Why this can pay when quote-polling doesn't: every 2-hop cycle the swarm
polls is priced within ~0.02% of efficiency between our snapshots. But a
single large swap through a thin pool dislocates its price for seconds to
minutes before arbitrageurs close it — and the swarm's ~13s cycle + full
232-route evaluation is too slow to catch the good ones. This watcher
learns each pool's price from the Swap event itself (free — the event
carries sqrtPriceX96), and only spends RPC on state fetches + exact quotes
when cross-pool skew actually exceeds the round-trip fees + floor.

Architecture mirrors scripts/newpool_sniper.py (same quoting, same cand
shape, same live_trader.fire path) so the fork-tested firing machinery is
unchanged.

Run from repo root:  python3 scripts/swap_watcher.py
Events log to run/swap_watch_events.json.
"""
import json
import sys
import time
from fractions import Fraction
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from zero.keccak import keccak256, selector_hex
from zero.rpc import decode_uints, encode_address, encode_uint, Rpc
from zero.aave import AaveV3
from zero.swarm import build_token_registry
from zero.uniswap_v3 import UniswapV3Pool
from zero.live_executor import check_gate, GateDenied
import live_trader

CFG = json.loads((REPO / "config" / "arbitrum.json").read_text())
EVENTS_PATH = REPO / "run" / "swap_watch_events.json"

PROVIDER = "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb"   # Aave pool provider
UNISWAP_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
SUSHI_FACTORY = "0x1af415a1EbA07a4986a52B6f2e7dE7003D82231e"
FACTORIES = [UNISWAP_FACTORY, SUSHI_FACTORY]
FEE_TIERS = [100, 500, 3000, 10000]

GAS_COST_USD = 0.071        # measured: ~700k @ 0.02 gwei, ETH ~$2600
RESERVE_USD = 0.05          # same conservatism as the engine's model reserve
MIN_NET_USD = 5.0           # matches config/live.json gate
SIZES_USD = [500.0, 2500.0]  # fixed cost floor needs >=$500 notional
SWAP_POLL_SECONDS = 2
PAIR_COOLDOWN = 15          # min seconds between evaluations of one pair
SKEW_HEADROOM = 0.0005      # skew must beat fees + this (0.05%)

LOGS_PRIMARY = "https://arb1.arbitrum.io/rpc"
LOGS_FALLBACK = "https://arbitrum-one.public.blastapi.io"
BLAST_CHUNK = 10

SWAP_TOPIC0 = "0x" + keccak256(
    b"Swap(address,address,int256,int256,uint128,uint160,uint128)").hex()


def log(event: dict) -> None:
    try:
        events = json.loads(EVENTS_PATH.read_text()) if EVENTS_PATH.exists() else []
    except Exception:
        events = []
    events.append(event)
    EVENTS_PATH.write_text(json.dumps(events[-1500:], indent=1))
    print(json.dumps(event), flush=True)


def ev_call(rpc, to, data, block="latest"):
    try:
        return rpc.call("eth_call", [{"to": to, "data": data}, block])
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


def pool_state(rpc, pool, block="latest"):
    try:
        return UniswapV3Pool(rpc, pool, None, None, 0, 0, 0).fetch_state(
            block=block)
    except Exception:
        return None


def quote_from_state(state, fee_percent, dec_in, dec_out, token_in_is_t0,
                     amount_in):
    """Exact single-range quote from a pinned state. Human out, or None."""
    if not state or state["liquidity"] <= 0:
        return None
    direction = "0to1" if token_in_is_t0 else "1to0"
    res = UniswapV3Pool.quote(state, amount_in / 10 ** dec_in, direction,
                              dec_in, dec_out, fee_percent)
    return None if res["out_of_range"] else res["out"]


def price_from_sqrt(sq, dec0, dec1):
    """Human token1-per-token0 price from sqrtPriceX96 (exact Fraction)."""
    if not sq:
        return None
    raw = Fraction(int(sq) * int(sq), 1 << 192)
    return raw * (Fraction(10) ** (dec0 - dec1))


def discover_pools(rpc, registry):
    """All uni/sushi pools among allowlisted pairs where one side is WETH or
    both sides are stables (the cycles the swarm routes already trust)."""
    addrs = {t.address: t for t in registry.values()}
    weth = next((t.address for s, t in registry.items()
                 if s == "WETH"), None)
    stables = [t.address for s, t in registry.items()
               if s in ("USDC", "USD₮0", "USDC.e", "DAI")]
    pairs = set()
    if weth:
        for addr in addrs:
            if addr != weth:
                pairs.add(frozenset((weth, addr)))
    for i, s1 in enumerate(stables):
        for s2 in stables[i + 1:]:
            pairs.add(frozenset((s1, s2)))

    pools_by_pair = {}
    for pair in pairs:
        a, b = sorted(pair)
        if a not in addrs or b not in addrs:
            continue
        pools = []
        for fac in FACTORIES:
            for fee in FEE_TIERS:
                p = get_pool(rpc, fac, a, b, fee)
                if p:
                    pools.append({"addr": p, "factory": fac, "fee": fee,
                                  "t0": a, "t1": b,
                                  "dec0": addrs[a].decimals,
                                  "dec1": addrs[b].decimals})
        if pools:
            pools_by_pair[pair] = pools
    return pools_by_pair


def evaluate_pair(rpc, pools, registry, head):
    """Both cycle directions over every ordered pair of pools. Returns the
    best candidate dict (sniper cand shape) or None."""
    prices = {t.address: t.price_usd for t in registry.values()}
    best = None
    states = {p["addr"]: pool_state(rpc, p["addr"], block=head)
              for p in pools}
    for i, pa in enumerate(pools):
        for pb in pools:
            if pa["addr"] == pb["addr"]:
                continue
            for asset_is_t0 in (True, False):
                # hop1 on pa: asset -> mid; hop2 on pb: mid -> asset
                if asset_is_t0:
                    dec_asset, dec_mid = pa["dec0"], pa["dec1"]
                    price_asset = prices[pa["t0"]]
                    sym_asset, sym_mid = pa["t0"], pa["t1"]
                else:
                    dec_asset, dec_mid = pa["dec1"], pa["dec0"]
                    price_asset = prices[pa["t1"]]
                    sym_asset, sym_mid = pa["t1"], pa["t0"]
                if price_asset <= 0:
                    continue
                fee_sum = (pa["fee"] + pb["fee"]) / 1e4
                for size_usd in SIZES_USD:
                    loan_size = size_usd / price_asset          # human asset
                    amount_in = int(round(loan_size * 10 ** dec_asset))
                    h1 = quote_from_state(
                        states[pa["addr"]], pa["fee"] / 1e4,
                        dec_in=dec_asset, dec_out=dec_mid,
                        token_in_is_t0=asset_is_t0, amount_in=amount_in)
                    if not h1 or h1 <= 0:
                        continue
                    h2_in = int(round(
                        h1 * (1 - live_trader.SWAP_BUFFER) * 10 ** dec_mid))
                    h2 = quote_from_state(
                        states[pb["addr"]], pb["fee"] / 1e4,
                        dec_in=dec_mid, dec_out=dec_asset,
                        token_in_is_t0=not asset_is_t0, amount_in=h2_in)
                    if not h2 or h2 <= 0:
                        continue
                    # flash premium is 0 (Balancer path)
                    net = (h2 - loan_size) * price_asset \
                        - GAS_COST_USD - RESERVE_USD
                    if best is None or net > best["net_usd"]:
                        best = {
                            "net_usd": net, "size_usd": size_usd,
                            "loan_size": loan_size,
                            "hop1_out": h1, "hop2_out": h2,
                            "gross_usd": (h2 - loan_size) * price_asset,
                            "asset": sym_asset, "mid": sym_mid,
                            "asset_is_t0": asset_is_t0,
                            "pools": [pa, pb], "fee_sum": fee_sum,
                        }
    return best


def build_candidate(best, head, registry):
    pa, pb = best["pools"]
    asset, mid = best["asset"], best["mid"]
    t_asset = next(t for t in registry.values() if t.address == asset)
    t_mid = next(t for t in registry.values() if t.address == mid)
    price_asset = t_asset.price_usd
    price_mid = t_mid.price_usd
    cand = {
        "id": f"swapwatch:{pa['addr']}:{int(time.time())}",
        "block": head,
        "asset": asset,
        "loan_size": best["loan_size"],
        "hop1_out": best["hop1_out"],
        "hop2_out": best["hop2_out"],
        "expected_net_usd": best["net_usd"],
        "name": (f"swapwatch {t_asset.symbol}->{t_mid.symbol} "
                 f"{pa['fee']/1e4}% | {t_mid.symbol}->{t_asset.symbol} "
                 f"{pb['fee']/1e4}%"),
        "route": {
            "base": pa["t0"] if best["asset_is_t0"] else pa["t1"],
            "quote": pa["t1"] if best["asset_is_t0"] else pa["t0"],
            "base_decimals": pa["dec0"] if best["asset_is_t0"] else pa["dec1"],
            "quote_decimals": pa["dec1"] if best["asset_is_t0"] else pa["dec0"],
            "base_price_usd": price_asset if best["asset_is_t0"] else price_mid,
            "quote_price_usd": price_mid if best["asset_is_t0"] else price_asset,
            "pools": [
                {"address": pa["addr"], "fee_tier": pa["fee"]},
                {"address": pb["addr"], "fee_tier": pb["fee"]},
            ],
        },
    }
    return cand


def fetch_swap_logs(rpc, addresses, frm, head):
    try:
        return Rpc(LOGS_PRIMARY).call("eth_getLogs", [{
            "fromBlock": hex(frm), "toBlock": hex(head),
            "address": addresses, "topics": [SWAP_TOPIC0]}])
    except Exception:
        out = []
        cur = frm
        fb = Rpc(LOGS_FALLBACK)
        while cur <= head:
            hi = min(cur + BLAST_CHUNK - 1, head)
            try:
                out.extend(fb.call("eth_getLogs", [{
                    "fromBlock": hex(cur), "toBlock": hex(hi),
                    "address": addresses, "topics": [SWAP_TOPIC0]}]))
            except Exception:
                pass
            cur = hi + 1
        return out


def main() -> None:
    rpc = Rpc("https://arbitrum-one.public.blastapi.io")
    aave = AaveV3(rpc, PROVIDER)
    head = int(rpc.call("eth_blockNumber", []), 16)
    registry = build_token_registry(aave, head)
    wanted = ["WETH", "USDC", "USD₮0", "WBTC", "ARB", "LINK",
              "wstETH", "rETH", "weETH", "ezETH", "rsETH"]
    registry = {s: t for s, t in registry.items() if s in wanted}
    log({"ts": time.time(), "event": "start",
         "tokens": sorted(registry)})

    pools_by_pair = discover_pools(rpc, registry)
    all_pools = []
    pair_of_pool = {}
    for pair, pools in pools_by_pair.items():
        for p in pools:
            pair_of_pool[p["addr"].lower()] = pair
        all_pools.extend(pools)
    log({"ts": time.time(), "event": "pools_resolved",
         "pairs": len(pools_by_pair), "pools": len(all_pools)})
    if not all_pools:
        log({"ts": time.time(), "event": "fatal", "error": "no pools"})
        return

    addresses = [p["addr"] for p in all_pools]
    # last human price seen per pool, from the Swap event's own sqrtPriceX96
    last_price = {p["addr"].lower(): None for p in all_pools}
    last_eval = {p["addr"].lower(): 0.0 for p in all_pools}

    last_block = head - 30        # small warmup
    while True:
        try:
            head = int(rpc.call("eth_blockNumber", []), 16)
            if head > last_block:
                logs = fetch_swap_logs(rpc, addresses, last_block + 1, head)
                for lg in logs or []:
                    try:
                        pool = "0x" + lg["address"].lower()[2:]
                        data = lg.get("data", "0x")
                        words = decode_uints(
                            bytes.fromhex(data[2:]) if isinstance(data, str)
                            else data)
                        if len(words) < 5:
                            continue
                        sq = words[2]
                        pinfo = next((p for p in all_pools
                                      if p["addr"].lower() == pool), None)
                        if pinfo is None:
                            continue
                        price = price_from_sqrt(sq, pinfo["dec0"],
                                                pinfo["dec1"])
                        prev = last_price[pool]
                        last_price[pool] = float(price) if price else None
                        if prev is None or price is None:
                            continue
                        # cross-pool skew against every counterpart's last price
                        pair = pair_of_pool[pool]
                        others = [last_price[p["addr"].lower()]
                                  for p in pools_by_pair[pair]
                                  if p["addr"].lower() != pool
                                  and last_price.get(p["addr"].lower())]
                        if not others:
                            continue
                        now_price = float(price)
                        # cheapest round trip available for this pair
                        min_fee = min(p["fee"] for p in pools_by_pair[pair])
                        threshold = 2 * min_fee / 1e4 + SKEW_HEADROOM
                        skew = max(abs(now_price / o - 1.0)
                                   for o in others if o > 0)
                        if skew < threshold:
                            continue
                        if time.time() - last_eval[pool] < PAIR_COOLDOWN:
                            continue
                        last_eval[pool] = time.time()
                        log({"ts": time.time(), "event": "dislocation",
                             "pool": pool, "pair": sorted(pair),
                             "skew": round(skew * 100, 4),
                             "threshold": round(threshold * 100, 3)})
                        best = evaluate_pair(
                            rpc, pools_by_pair[pair], registry, head)
                        if best is None:
                            log({"ts": time.time(), "event": "unquotable",
                                 "pool": pool})
                            continue
                        log({"ts": time.time(), "event": "evaluated",
                             "pair": sorted(pair), "best": {
                                 k: best[k] for k in
                                 ("net_usd", "size_usd", "gross_usd",
                                  "fee_sum")}})
                        if best["net_usd"] < MIN_NET_USD:
                            continue
                        cand = build_candidate(best, head, registry)
                        try:
                            check_gate(cand)
                        except GateDenied as exc:
                            log({"ts": time.time(), "event": "gate_denied",
                                 "reason": str(exc)})
                            continue
                        log({"ts": time.time(), "event": "firing",
                             "net_usd": best["net_usd"]})
                        live_trader.fire(rpc, cand, None)
                        time.sleep(30)
                    except Exception as exc:
                        log({"ts": time.time(), "event": "handle_error",
                             "error": str(exc)[:200]})
                last_block = head
            else:
                time.sleep(SWAP_POLL_SECONDS)
        except Exception as exc:
            log({"ts": time.time(), "event": "loop_error",
                 "error": str(exc)[:200]})
            time.sleep(SWAP_POLL_SECONDS * 2)
        time.sleep(SWAP_POLL_SECONDS)


if __name__ == "__main__":
    main()