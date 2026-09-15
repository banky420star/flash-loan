"""CLI helpers for ZERO Engine fork verification."""

from __future__ import annotations

import os
import shlex
import subprocess

from .fork import AnvilFork


def command_line(upstream_rpc: str, block_number: int, port: int = 8545) -> str:
    fork = AnvilFork(upstream_rpc=upstream_rpc, block_number=block_number, port=port)
    return shlex.join(fork.command())


def build_status(upstream_rpc: str, block_number: int, port: int = 8545) -> dict:
    fork = AnvilFork(upstream_rpc=upstream_rpc, block_number=block_number, port=port)
    return {
        "anvil_installed": fork.installed(),
        "block": block_number,
        "upstream_rpc": upstream_rpc,
        "local_rpc": fork.local_rpc_url,
        "command": shlex.join(fork.command()),
    }


def run_fork_test(upstream_rpc: str, block_number: int | None = None) -> int:
    env = os.environ.copy()
    env["ARBITRUM_RPC_URL"] = upstream_rpc
    if block_number is not None:
        env["FORK_BLOCK"] = str(block_number)
    else:
        env.pop("FORK_BLOCK", None)
    completed = subprocess.run(["bash", "scripts/fork_test.sh"], env=env)
    return completed.returncode


def run_live_candidate_fork(upstream_rpc: str, payload: dict) -> int:
    """Replay exactly one encoded PASS candidate on its detection block.

    Foundry uses the upstream RPC only as a fork source. The three calldata
    blobs are passed verbatim from Python into the Solidity test through
    environment variables; there is no signing or transaction broadcast path.
    """
    candidate = payload.get("candidate")
    steps = payload.get("steps")
    if not isinstance(candidate, dict):
        raise ValueError("candidate payload is required")
    if not isinstance(steps, list) or len(steps) != 3:
        raise ValueError("exactly three execution steps are required")
    block = int(candidate.get("block", 0))
    if block <= 0:
        raise ValueError("candidate block must be positive")
    asset = str(candidate.get("base_asset", ""))
    if not asset.startswith("0x") or len(asset) != 42:
        raise ValueError("candidate base asset must be an address")

    env = os.environ.copy()
    env["ARBITRUM_RPC_URL"] = upstream_rpc
    env["FORK_BLOCK"] = str(block)
    env["ZERO_ASSET"] = asset
    env["ZERO_LOAN_RAW"] = str(int(candidate["loan_amount_raw"]))
    env["ZERO_MIN_PROFIT_RAW"] = str(int(candidate["min_profit_raw"]))
    for i, step in enumerate(steps):
        if int(step.get("value", 0)) != 0:
            raise ValueError("v0.4 candidate steps must have zero ETH value")
        target = str(step.get("target", ""))
        data = str(step.get("data", ""))
        if not target.startswith("0x") or len(target) != 42:
            raise ValueError(f"step {i} target must be an address")
        if not data.startswith("0x"):
            raise ValueError(f"step {i} calldata must be hex-prefixed")
        env[f"ZERO_STEP{i}_TARGET"] = target
        env[f"ZERO_STEP{i}_DATA"] = data

    completed = subprocess.run(["bash", "scripts/live_candidate_fork_test.sh"], env=env)
    return completed.returncode
