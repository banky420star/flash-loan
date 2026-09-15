"""Conservative reserve learned from exact-fork prediction errors."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil


@dataclass(frozen=True)
class ReserveEstimate:
    value_usd: float
    samples: int


class AdaptiveReserve:
    """Estimate an adverse-error reserve from recent fork verification rows."""

    def __init__(self, *, lookback: int = 100, min_samples: int = 5,
                 quantile: float = 0.90, floor_usd: float = 0.0,
                 cap_usd: float = 25.0,
                 bootstrap_reserve_usd: float = 0.05):
        if lookback <= 0:
            raise ValueError("lookback must be positive")
        if min_samples < 0:
            raise ValueError("min_samples must be non-negative")
        if not 0 < quantile <= 1:
            raise ValueError("quantile must be in (0, 1]")
        if floor_usd < 0 or cap_usd < 0:
            raise ValueError("reserve bounds must be non-negative")
        if cap_usd < floor_usd:
            raise ValueError("cap_usd must be >= floor_usd")
        if bootstrap_reserve_usd < 0:
            raise ValueError("bootstrap_reserve_usd must be non-negative")
        self.lookback = int(lookback)
        self.min_samples = int(min_samples)
        self.quantile = float(quantile)
        self.floor = Decimal(str(floor_usd))
        self.cap = Decimal(str(cap_usd))
        self.bootstrap = Decimal(str(bootstrap_reserve_usd))

    def _clamp(self, value: Decimal) -> Decimal:
        return min(self.cap, max(self.floor, value))

    @staticmethod
    def _adverse_sample(row: dict) -> Decimal:
        predicted = Decimal(str(row.get("predicted_net", 0.0) or 0.0))
        realized = Decimal(str(row.get("realized_net", 0.0) or 0.0))
        model_error = row.get("model_error")
        if model_error is None:
            error = realized - predicted
        else:
            error = Decimal(str(model_error or 0.0))
        adverse = max(Decimal("0"), -error)

        # A failed candidate with no measured realized P&L still consumed a
        # positive predicted edge. Treat that full edge as adverse evidence so
        # repeated exact-fork false positives cannot remain cost-free.
        if not bool(row.get("success", False)) and predicted > 0 and realized <= 0:
            adverse = max(adverse, predicted)
        return adverse

    def estimate(self, rows: list[dict]) -> ReserveEstimate:
        limited = list(rows[:self.lookback])
        samples = len(limited)
        if samples < self.min_samples:
            value = self._clamp(max(self.floor, self.bootstrap))
            return ReserveEstimate(float(value), samples)

        adverse = sorted(self._adverse_sample(row) for row in limited)
        if not adverse:
            return ReserveEstimate(float(self._clamp(self.floor)), 0)

        rank = max(1, ceil(self.quantile * len(adverse)))
        value = self._clamp(adverse[rank - 1])
        return ReserveEstimate(float(value), samples)
