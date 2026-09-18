"""Build a liquidation watchlist from Aave V3 Arbitrum Borrow events.

Backfill mode indexes historical Borrow events and records current account
data for every address that still holds debt, so the live engine can watch
addresses that are actually close to their liquidation threshold.

    python3 scripts/build_liquidation_watchlist.py --days 90 \
        --out run/liquidation-watchlist.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zero.aave import AaveV3
from zero.keccak import topic_of
from zero.rpc import Rpc, RpcError, UrllibTransport

from zero.aave import AaveV3
from zero.rpc import Rpc, RpcError, UrllibTransport

# Aave V3 Pool.Borrow(address indexed reserve, address user,
#   address indexed onBehalfOf, uint256 amount, uint8 interestRateMode,
#   uint256 borrowRate, uint16 indexed referralCode)
# The deployed pool canonicalizes the InterestRateMode enum to uint8, so the
# on-chain topic differs from the source-level signature.
BORROW_TOPIC = ("0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465"
                "bc7359d7dce0")


def backfill_borrowers(rpc: Rpc, pool: str, head: int, days: int) -> set[str]:
    from_block = head - int(days * 24 * 3600 * 4)  # ~0.25s blocks
    borrowers: set[str] = set()
    failures = 0
    chunk = 10000
    for lo in range(from_block, head, chunk):
        hi = min(lo + chunk - 1, head)
        for attempt in range(5):
            try:
                logs = rpc.call("eth_getLogs", [{
                    "address": pool,
                    "topics": [BORROW_TOPIC],
                    "fromBlock": hex(lo),
                    "toBlock": hex(hi),
                }])
                for log in logs:
                    borrower = "0x" + log["topics"][2][-40:]
                    borrowers.add(borrower.lower())
                break
            except Exception as exc:
                failures += 1
                if attempt == 4:
                    print(f"chunk {lo}-{hi} failed: {exc}", file=sys.stderr)
                import time
                time.sleep(1.5 * (attempt + 1))
        if len(borrowers) % 2000 < 40 and borrowers:
            print(f"  {lo}: {len(borrowers)} borrowers", flush=True)
    print(f"backfill: {len(borrowers)} distinct borrowers, "
          f"{failures} failed chunks")
    return borrowers


def scan_health(rpc: Rpc, pool: str, borrowers: list[str],
                block: int, batch_size: int = 40) -> list[dict]:
    from zero.keccak import selector_hex
    from zero.rpc import decode_uints, encode_address
    rows = []
    for start in range(0, len(borrowers), batch_size):
        batch = borrowers[start:start + batch_size]
        calls = [(pool, selector_hex("getUserAccountData(address)")
                  + encode_address(b)[2:]) for b in batch]
        try:
            results = rpc.batch_eth_call_results(calls, block=block)
        except Exception as exc:
            print(f"batch {start} failed: {exc}", file=sys.stderr)
            continue
        for borrower, data in zip(batch, results):
            if isinstance(data, RpcError) or not data:
                continue
            words = decode_uints(data)
            if len(words) < 6:
                continue
            collateral_usd, debt_usd, _, _, _, hf = words[:6]
            if debt_usd <= 0:
                continue
            rows.append({
                "borrower": borrower,
                "collateral_usd": collateral_usd / 1e8,
                "debt_usd": debt_usd / 1e8,
                "health_factor": hf / 1e18,
            })
        if (start // batch_size) % 25 == 0:
            print(f"  scanned {start + len(batch)}/{len(borrowers)}",
                  flush=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--out", default="run/liquidation-watchlist.json")
    parser.add_argument("--rpc", default="https://arb1.arbitrum.io/rpc")
    args = parser.parse_args()

    rpc = Rpc(args.rpc, transport=UrllibTransport(args.rpc, timeout=30.0))
    provider = json.load(open(Path(__file__).resolve().parent.parent
                              / "config" / "arbitrum.json"))["aave_provider"]
    aave = AaveV3(rpc, provider)
    pool = aave.pool_address("latest")
    head = rpc.block_number()
    print(f"head {head}, pool {pool}")

    borrowers = sorted(backfill_borrowers(rpc, pool, head, args.days))
    print("scanning health factors ...", flush=True)
    rows = scan_health(rpc, pool, borrowers, head)
    rows.sort(key=lambda r: r["health_factor"])
    buckets = {
        "liquidatable_hf_le_1": 0,
        "hf_1_to_1_1": 0,
        "hf_1_1_to_1_3": 0,
        "hf_1_3_to_2": 0,
        "hf_gt_2": 0,
    }
    for row in rows:
        hf = row["health_factor"]
        if hf <= 1.0:
            buckets["liquidatable_hf_le_1"] += 1
        elif hf <= 1.1:
            buckets["hf_1_to_1_1"] += 1
        elif hf <= 1.3:
            buckets["hf_1_1_to_1_3"] += 1
        elif hf <= 2.0:
            buckets["hf_1_3_to_2"] += 1
        else:
            buckets["hf_gt_2"] += 1
    print(json.dumps({
        "block": head,
        "borrowers_with_debt": len(rows),
        "hf_buckets": buckets,
    }, indent=1))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({
        "block": head,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "buckets": buckets,
        "borrowers": rows,
    }, indent=1))
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()