"""CLI helpers for ZERO Engine fork verification."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from .fork import (
    AnvilFork, ForkResult, OUTCOME_EXECUTION_REVERT,
    OUTCOME_INFRASTRUCTURE_ERROR, OUTCOME_INVALID_HARNESS,
)


RESULT_DIR = "out/zero-results"


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


def _validate_payload(payload: dict) -> tuple[dict, list, int, str]:
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
    return candidate, steps, block, asset


def _candidate_env(upstream_rpc: str, payload: dict,
                   result_path: str | None = None) -> tuple[dict, dict, int]:
    candidate, steps, block, asset = _validate_payload(payload)
    env = os.environ.copy()
    env["ARBITRUM_RPC_URL"] = upstream_rpc
    env["FORK_BLOCK"] = str(block)
    env["ZERO_ASSET"] = asset
    env["ZERO_LOAN_RAW"] = str(int(candidate["loan_amount_raw"]))
    env["ZERO_MIN_PROFIT_RAW"] = str(int(candidate["min_profit_raw"]))
    if result_path is not None:
        env["ZERO_RESULT_PATH"] = result_path
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
    return env, candidate, block


def _new_result_path() -> str:
    Path(RESULT_DIR).mkdir(parents=True, exist_ok=True)
    return str(Path(RESULT_DIR) / f"candidate-{uuid.uuid4().hex}.json")


def run_live_candidate_fork(upstream_rpc: str, payload: dict) -> int:
    """Replay one encoded candidate and preserve the legacy integer return code."""
    result_path = _new_result_path()
    env, _, _ = _candidate_env(upstream_rpc, payload, result_path=result_path)
    try:
        completed = subprocess.run(
            ["bash", "scripts/live_candidate_fork_test.sh"], env=env)
        return completed.returncode
    finally:
        try:
            Path(result_path).unlink()
        except FileNotFoundError:
            pass


def _classify_failed_replay(completed) -> str:
    stdout = (getattr(completed, "stdout", "") or "")
    stderr = (getattr(completed, "stderr", "") or "")
    text = (stdout + "\n" + stderr).lower()
    if "stepfailed(" in text or "minimumprofitnotmet(" in text:
        return OUTCOME_EXECUTION_REVERT
    infrastructure_markers = (
        "forge is required", "foundry", "too many requests", "http 429",
        "error sending request", "connection", "network", "rpc",
        "timed out", "timeout", "unreachable",
    )
    if any(marker in text for marker in infrastructure_markers):
        return OUTCOME_INFRASTRUCTURE_ERROR
    return OUTCOME_INFRASTRUCTURE_ERROR


def run_live_candidate_fork_result(upstream_rpc: str, payload: dict) -> ForkResult:
    """Replay one candidate and return machine-readable fork economics.

    `realized_raw` is the base-token balance retained by ZeroForkExecutor after
    Aave principal and premium are repaid. `gas_used` is measured around the
    executor call inside the Foundry test. The USD gas estimate scales the
    scanner's modeled gas budget by measured gas / configured gas limit; it is
    a fork execution estimate, not a claim about future mainnet inclusion cost.
    """
    result_path = _new_result_path()
    env, candidate, block = _candidate_env(
        upstream_rpc, payload, result_path=result_path)
    verification = payload.get("verification") or {}
    reserve_usd = float(verification.get("model_reserve_usd", 0.0))
    predicted_gate_net = float(candidate.get("predicted_net", 0.0))
    predicted_model_net = predicted_gate_net + reserve_usd
    strategy = str(verification.get("strategy", "swarm_arbitrage"))

    completed = None
    try:
        completed = subprocess.run(
            ["bash", "scripts/live_candidate_fork_test.sh"],
            env=env,
            capture_output=True,
            text=True,
        )
        base_detail = {
            "candidate_id": verification.get("candidate_id"),
            "route_id": verification.get("route_id"),
            "returncode": int(completed.returncode),
            "admission_expected_net_usd": predicted_gate_net,
            "model_reserve_usd": reserve_usd,
        }
        if completed.returncode != 0:
            base_detail["stdout_tail"] = (getattr(completed, "stdout", "") or "")[-2000:]
            base_detail["stderr_tail"] = (getattr(completed, "stderr", "") or "")[-2000:]
            return ForkResult(
                block=block,
                strategy=strategy,
                success=False,
                gas_used=0,
                predicted_net=predicted_model_net,
                realized_net=0.0,
                detail=json.dumps(base_detail, sort_keys=True),
                outcome_class=_classify_failed_replay(completed),
            )

        path = Path(result_path)
        if not path.exists():
            base_detail["error"] = "missing_result_file"
            return ForkResult(
                block=block,
                strategy=strategy,
                success=False,
                gas_used=0,
                predicted_net=predicted_model_net,
                realized_net=0.0,
                detail=json.dumps(base_detail, sort_keys=True),
                outcome_class=OUTCOME_INVALID_HARNESS,
            )

        raw = json.loads(path.read_text())
        realized_raw = int(raw["realized_raw"])
        gas_used = int(raw["gas_used"])
        decimals = int(candidate["base_decimals"])
        base_price_usd = float(verification.get("base_price_usd", 1.0))
        realized_token_profit = realized_raw / (10 ** decimals)
        realized_profit_usd = realized_token_profit * base_price_usd

        modeled_gas_usd = float(candidate.get("gas_cost_usd", 0.0))
        gas_limit = int(verification.get("gas_limit", 0) or 0)
        if gas_limit > 0:
            estimated_gas_cost_usd = modeled_gas_usd * gas_used / gas_limit
        else:
            estimated_gas_cost_usd = modeled_gas_usd
        realized_net_usd = realized_profit_usd - estimated_gas_cost_usd

        base_detail.update({
            "realized_raw": str(realized_raw),
            "realized_token_profit": realized_token_profit,
            "base_price_usd": base_price_usd,
            "realized_profit_usd": realized_profit_usd,
            "fork_execution_gas_used": gas_used,
            "modeled_gas_budget_usd": modeled_gas_usd,
            "estimated_gas_cost_usd": estimated_gas_cost_usd,
        })
        return ForkResult(
            block=block,
            strategy=strategy,
            success=True,
            gas_used=gas_used,
            predicted_net=predicted_model_net,
            realized_net=realized_net_usd,
            detail=json.dumps(base_detail, sort_keys=True),
        )
    except Exception as exc:
        detail = {
            "candidate_id": verification.get("candidate_id"),
            "route_id": verification.get("route_id"),
            "returncode": int(getattr(completed, "returncode", -1)),
            "error": f"{type(exc).__name__}: {exc}",
            "admission_expected_net_usd": predicted_gate_net,
            "model_reserve_usd": reserve_usd,
        }
        return ForkResult(
            block=block,
            strategy=strategy,
            success=False,
            gas_used=0,
            predicted_net=predicted_model_net,
            realized_net=0.0,
            detail=json.dumps(detail, sort_keys=True),
            outcome_class=(
                OUTCOME_INVALID_HARNESS
                if completed is not None and getattr(completed, "returncode", -1) == 0
                else OUTCOME_INFRASTRUCTURE_ERROR
            ),
        )
    finally:
        try:
            Path(result_path).unlink()
        except FileNotFoundError:
            pass
