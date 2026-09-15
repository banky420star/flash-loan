"""Liquidation scanner: watchlist health-factor monitor + candidate P&L.

v0.2 scope: watchlist addresses only (config-driven). Full borrower indexing
from Transfer/Supply events is the next stage, not this one.
"""

from ..aave import bucket_for
from ..gate import Gate

DEFAULT_CLOSE_FACTOR = 0.5   # simplified Aave V3 close factor
DEFAULT_BONUS = 0.05         # placeholder collateral bonus; config overrides


def evaluate_account(account_data: dict, bonus: float = DEFAULT_BONUS,
                     close_factor: float = DEFAULT_CLOSE_FACTOR) -> dict:
    """Turn raw getUserAccountData into a bucketed, decision-ready record."""
    hf = account_data["health_factor"]
    record = dict(account_data)
    record["bucket"] = bucket_for(hf)

    if hf < 1.0 and account_data["debt_usd"] > 0:
        max_repay = close_factor * account_data["debt_usd"]
        est_profit = max_repay * bonus
        record.update({
            "liquidatable": True,
            "max_repay_usd": max_repay,
            "est_gross_profit_usd": est_profit,
            "bonus_assumed": bonus,
        })
    else:
        record.update({
            "liquidatable": False,
            "max_repay_usd": 0.0,
            "est_gross_profit_usd": 0.0,
            "bonus_assumed": bonus,
        })
    return record


def scan_watchlist(aave, rpc, pool: str, watchlist: list,
                   bonus: float = DEFAULT_BONUS) -> list:
    """Pull account data for every watchlist address; return bucketed records."""
    records = []
    for addr in watchlist:
        raw = aave.account_data(pool, addr)
        raw["address"] = addr
        records.append(evaluate_account(raw, bonus=bonus))
    return records


def gate_liquidations(records: list, gate: Gate, gas_cost_usd: float,
                      discovered_at_ms: float) -> list:
    """Run liquidatable records through the risk gate."""
    decisions = []
    for r in records:
        if not r["liquidatable"]:
            continue
        gross = r["est_gross_profit_usd"]
        net = gross - gas_cost_usd
        decision, reason, min_profit = gate.evaluate(
            net, gross, gas_cost_usd, r["max_repay_usd"],
            discovered_at_ms=discovered_at_ms)
        decisions.append({"address": r["address"], "hf": r["health_factor"],
                          "gross": gross, "net": net,
                          "decision": decision, "reason": reason})
    return decisions