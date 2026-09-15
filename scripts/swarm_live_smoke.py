#!/usr/bin/env python3
"""Live Arbitrum smoke for ZERO's pinned batched swarm market-data path."""

import json
import os
import tempfile

from zero.cli import load_config
from zero.engine import ShadowEngine
from zero.ledger import Ledger
from zero.pnl import PnlSwarmSupervisor


def main() -> int:
    cfg = load_config()
    # This smoke validates live catalog/context/scanning only. Fork execution is
    # covered by the dedicated Aave and candidate-fork jobs.
    cfg["swarm"]["verify_positive_candidates"] = False

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
