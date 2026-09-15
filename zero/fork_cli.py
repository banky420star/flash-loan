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
