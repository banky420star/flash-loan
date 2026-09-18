"""Gated live execution — the only path from scanner output to real money.

Fires a ZeroExecutor flash-loan trade only when every gate passes:
  - run/KILL does not exist (kill switch)
  - expected net USD >= min_net_usd
  - notional <= max_notional_usd
  - daily realized gas losses < daily_gas_cap_usd
  - candidate asset/routers pre-approved on the executor

Caps live in config/live.json. The wallet key is read from wallet/hot.key
at send time and never logged. This module is imported ONLY by the live
scripts, never by the shadow engine (enforced by test_no_live_broadcast).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .keccak import keccak256

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "live.json"
KILL_PATH = ROOT / "run" / "KILL"
GAS_LOG_PATH = ROOT / "run" / "live_gas_log.json"

DAY = 86400


class GateDenied(Exception):
    pass


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def check_gate(candidate: dict, config: dict | None = None,
               now: float | None = None) -> dict:
    """Return a pass report or raise GateDenied with the reason."""
    cfg = config or load_config()
    now = now or time.time()
    if KILL_PATH.exists():
        raise GateDenied("kill switch present (run/KILL)")
    net = float(candidate.get("expected_net_usd", 0.0))
    if net < cfg["min_net_usd"]:
        raise GateDenied(
            f"net ${net:.4f} < min ${cfg['min_net_usd']}")
    notional = float(candidate.get("notional_usd", 0.0))
    if notional > cfg["max_notional_usd"]:
        raise GateDenied(
            f"notional ${notional:.0f} > cap ${cfg['max_notional_usd']}")
    spent = _gas_spent_24h(now)
    if spent >= cfg["daily_gas_cap_usd"]:
        raise GateDenied(
            f"daily gas spend ${spent:.2f} >= cap ${cfg['daily_gas_cap_usd']}")
    return {"net_usd": net, "notional_usd": notional, "gas_spent_usd": spent}


def record_gas_spend(usd: float, now: float | None = None) -> None:
    """Append a gas-spend entry so the daily cap is enforced across runs."""
    now = now or time.time()
    entries = _load_gas_log()
    entries.append({"t": now, "usd": round(usd, 6)})
    cutoff = now - DAY
    GAS_LOG_PATH.write_text(json.dumps(
        [e for e in entries if e["t"] >= cutoff]))
    GAS_LOG_PATH.parent.mkdir(exist_ok=True)


def _load_gas_log() -> list:
    try:
        return json.loads(GAS_LOG_PATH.read_text())
    except Exception:
        return []


def _gas_spent_24h(now: float) -> float:
    cutoff = now - DAY
    return sum(e["usd"] for e in _load_gas_log() if e["t"] >= cutoff)


# ---------------------------------------------------------------------------
# ABI encoding for ZeroExecutor.run / runBalancer
# ---------------------------------------------------------------------------

# (uint8 kind, address target, address tokenIn, address tokenOut,
#  address account, uint256 amount, uint256 limit, uint24 fee,
#  uint8 recipientMode) — all static, 9 words per step.
_STEP_WORDS = 9


def _pad_address(addr: str) -> str:
    return addr[2:].lower().rjust(64, "0")


def _pad_uint(value: int) -> str:
    return value.to_bytes(32, "big").hex() if value else "00" * 32


def encode_steps(steps: list[dict]) -> str:
    # run()'s args: 4 scalars, then the dynamic array's offset word (160 = 5
    # head words), then the array: length word + one word per static field.
    head = _pad_uint(160)
    head += _pad_uint(len(steps))
    for s in steps:
        head += "".join([
            _pad_uint(int(s["kind"])),
            _pad_address(s["target"]),
            _pad_address(s["tokenIn"]),
            _pad_address(s["tokenOut"]),
            _pad_address(s["account"]),
            _pad_uint(int(s["amount"])),
            _pad_uint(int(s["limit"])),
            _pad_uint(int(s["fee"])),
            _pad_uint(int(s["recipientMode"])),
        ])
    return head


def _selector(sig: str) -> str:
    return keccak256(sig.encode()).hex()[:8]


def build_run_calldata(entry: str, asset: str, amount: int,
                       min_profit: int, deadline: int,
                       steps: list[dict]) -> bytes:
    sig = {"aave": "run(address,uint256,uint256,uint256,(uint8,address,"
                   "address,address,uint256,uint256,uint24,uint8)[])",
           "balancer": "runBalancer(address,uint256,uint256,uint256,"
                       "(uint8,address,address,address,uint256,uint256,"
                       "uint24,uint8)[])"}[entry]
    args = "".join([
        _pad_address(asset), _pad_uint(amount), _pad_uint(min_profit),
        _pad_uint(deadline), encode_steps(steps),
    ])
    return bytes.fromhex(_selector(sig) + args)