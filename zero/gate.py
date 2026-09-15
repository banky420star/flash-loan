"""Risk gate: every candidate passes through here before the ledger.

The gate is deliberately dumb and strict. It never sees execution — its only
outputs are PASS / REJECT with a reason.
"""

import time


class Gate:
    def __init__(self, floor_usd: float = 2.0, gas_multiple: float = 4.0,
                 min_roi: float = 0.0001, max_age_ms: int = 500):
        self.floor_usd = floor_usd
        self.gas_multiple = gas_multiple
        self.min_roi = min_roi
        self.max_age_ms = max_age_ms

    def min_profit(self, gas_cost_usd: float, loan_size_usd: float) -> float:
        """minProfit = max(floor, k * gas, roi * size)."""
        return max(self.floor_usd,
                   self.gas_multiple * gas_cost_usd,
                   self.min_roi * loan_size_usd)

    def evaluate(self, net_profit: float, gross_profit: float,
                 gas_cost_usd: float, loan_size_usd: float,
                 discovered_at_ms: float | None = None,
                 now_ms: float | None = None) -> tuple:
        """Returns (decision: 'PASS'|'REJECT', reason: str, min_profit: float)."""
        min_profit = self.min_profit(gas_cost_usd, loan_size_usd)

        if gross_profit <= 0:
            return "REJECT", "negative_gross", min_profit
        if net_profit < min_profit:
            return "REJECT", f"net {net_profit:.4f} < min {min_profit:.4f}", min_profit
        if discovered_at_ms is not None:
            now = now_ms if now_ms is not None else time.time() * 1000
            age_ms = now - discovered_at_ms
            if age_ms > self.max_age_ms:
                return "REJECT", f"stale: age {age_ms:.0f}ms > {self.max_age_ms}ms", min_profit
        return "PASS", "ok", min_profit