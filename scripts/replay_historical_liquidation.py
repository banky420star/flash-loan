"""Replay one historical Aave liquidation through the real ZERO pipeline.

Builds the liquidation state at the block just before the on-chain
LiquidationCall, quotes it exactly as the live watchlist scanner does, then
fork-verifies the encoded payload with the production liquidation harness.
This is evidence, not a backtest: the executor either captures the 5% bonus
on the fork or it does not.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zero.cli import load_config
from zero.engine import ShadowEngine
from zero.fork_cli import run_live_liquidation_fork_result
from zero.ledger import Ledger
from zero.liquidation_calldata import build_liquidation_steps
from zero.liquidation_swarm import scan_liquidation_watchlist


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--block", type=int, required=True,
                        help="block containing the LiquidationCall")
    parser.add_argument("--borrower", required=True)
    parser.add_argument("--collateral", required=True)
    parser.add_argument("--debt", required=True)
    parser.add_argument("--rpc", default=None)
    args = parser.parse_args()

    cfg = load_config()
    if args.rpc:
        cfg["rpc_url"] = args.rpc
        cfg["rpc_urls"] = [args.rpc]

    with tempfile.TemporaryDirectory() as tmp:
        engine = ShadowEngine(cfg["rpc_url"], cfg, Ledger(f"{tmp}/l.db"))
        venues = getattr(engine, "venues", {})
        if not venues:
            raise SystemExit("engine has no venue registry")
        pre_block = args.block - 1
        context = SimpleNamespace(
            block=pre_block,
            premium_bps=int(engine.aave.flashloan_premium_total(
                engine.aave.pool_address(block=pre_block), block=pre_block)),
            gas_usd=0.5,
        )
        config = {**cfg, "liquidation": {**cfg.get("liquidation", {}),
                                         "watchlist": [args.borrower.lower()]}}
        candidates, errors = scan_liquidation_watchlist(
            engine, config, context, venues)
        for error in errors:
            print("scan error:", json.dumps(error))
        if not candidates:
            raise SystemExit("no liquidation candidate at the pre-block — "
                             "position was not liquidatable or unwind route "
                             "was not executable")
        best = max(candidates, key=lambda c: c.expected_net)
        raw = best.payload["candidate"]
        print("quoted candidate:")
        for key in ("predicted_net", "loan_size", "gas_cost_usd",
                    "debt_to_cover_raw", "collateral_received_raw",
                    "unwind_out_raw", "min_profit_raw"):
            print(f"  {key}: {raw.get(key)}")

        pool = engine.aave.pool_address(block=pre_block)
        execution = cfg["arbitrage"]["execution"]
        steps = build_liquidation_steps(
            raw, pool, execution["swap_router_02"],
            slippage_bps=int(execution.get("slippage_bps", 20)))
        payload = {
            "kind": "liquidation",
            "candidate": dict(raw),
            "steps": [step.as_dict() for step in steps],
            "verification": {
                "candidate_id": best.candidate_id,
                "route_id": best.route_id,
                "strategy": "replay_liquidation",
                "base_price_usd": float(raw["base_price_usd"]),
                "model_reserve_usd": 0.0,
                "gas_limit": int(cfg.get("gas_limit", 0) or 0),
            },
        }
        result = run_live_liquidation_fork_result(cfg["rpc_url"], payload)
        print("fork result:")
        print(json.dumps(result.as_dict(), indent=1))


if __name__ == "__main__":
    main()