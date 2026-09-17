#!/usr/bin/env python3
"""Live Arbitrum smoke for ZERO's pinned batched swarm market-data path."""

import json
import os
import tempfile

from zero.cli import load_config
from zero.engine import ShadowEngine
from zero.ledger import Ledger
from zero.pnl import PnlSwarmSupervisor


def configure_marketdata_smoke(cfg: dict) -> dict:
    """Scope live CI to the bounded batched Uniswap market-data path."""
    swarm = cfg.setdefault("swarm", {})
    swarm["verify_positive_candidates"] = False
    swarm["multihop_enabled"] = False
    for venue_id, venue in (cfg.get("venues", {}) or {}).items():
        if venue_id != "uniswap_v3":
            venue["enabled"] = False
    return cfg


def main() -> int:
    cfg = load_config()
    rpc_override = os.environ.get("ZERO_LIVE_RPC_URL")
    if rpc_override:
        cfg["rpc_url"] = rpc_override
    # Keep this job a bounded batched market-data smoke. Multi-DEX route
    # economics are deterministic unit-test coverage; exercising every exact
    # venue quote here turns a provider smoke into a public-RPC stress test.
    configure_marketdata_smoke(cfg)

    with tempfile.TemporaryDirectory(prefix="zero-swarm-smoke-") as tmp:
        ledger = Ledger(os.path.join(tmp, "ledger.db"))
        try:
            engine = ShadowEngine(cfg["rpc_url"], cfg, ledger)
            supervisor = PnlSwarmSupervisor(engine, cfg, ledger)
            result = supervisor.run_block()
        finally:
            ledger.close()

    assert result["active_workers"] == 20, result
    assert result["routes_scanned"] >= 0, result
    assert result["worker_failures"] == 0, result
    assert result["catalog_ms"] >= 0, result
    assert result["scan_ms"] >= 0, result
    assert result["verify_ms"] >= 0, result
    assert result["reserve_samples"] >= 0, result
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
