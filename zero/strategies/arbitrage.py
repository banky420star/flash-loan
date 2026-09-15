"""Atomic arbitrage scanner: 2-hop cycles between Uniswap V3 pools.

For each cycle we sweep loan sizes geometrically, quote both hops exactly,
and keep the size with the best GROSS profit. Gas and the profit gate are
applied downstream by the engine — the scanner stays pure.
"""

import math
from ..uniswap_v3 import UniswapV3Pool

SWEEP_STEPS = 24


def sweep_sizes(min_usd: float, max_usd: float, steps: int = SWEEP_STEPS) -> list:
    lo = math.log10(max(min_usd, 1e-6))
    hi = math.log10(max_usd)
    return [10 ** (lo + (hi - lo) * i / (steps - 1)) for i in range(steps)]


class Cycle:
    """Two hops on the same token pair: base -> quote (pool A) -> base (pool B)."""

    def __init__(self, pool_a, pool_b, base: str, base_decimals: int,
                 quote_decimals: int):
        if {pool_a.token0, pool_a.token1} != {pool_b.token0, pool_b.token1}:
            raise ValueError("cycle pools must share the same token pair")
        base = base.lower()
        self.pool_a = pool_a
        self.pool_b = pool_b
        self.base = base
        self.base_decimals = base_decimals
        self.quote_decimals = quote_decimals
        # Direction per pool: base is either token0 or token1 on each pool.
        self._dir_a = "0to1" if pool_a.token0.lower() == base else "1to0"
        self._dir_b = "1to0" if pool_b.token0.lower() == base else "0to1"

    def profit_curve(self, state_a: dict, state_b: dict, sizes: list) -> list:
        """Gross profit (base units) per loan size. -inf marks out-of-range."""
        curve = []
        for q in sizes:
            r1 = UniswapV3Pool.quote(state_a, q, self._dir_a, self.base_decimals,
                                     self.quote_decimals, self.pool_a.fee_percent)
            r2 = UniswapV3Pool.quote(state_b, r1["out"], self._dir_b,
                                     self.quote_decimals, self.base_decimals,
                                     self.pool_b.fee_percent)
            if r1["out_of_range"] or r2["out_of_range"]:
                curve.append({"size": q, "gross": float("-inf"), "out_of_range": True})
            else:
                curve.append({"size": q, "gross": r2["out"] - q, "out_of_range": False})
        return curve


def best_opportunity(curve: list) -> dict | None:
    """Argmax over the profit curve; None when no size yields positive gross."""
    candidates = [c for c in curve if not c["out_of_range"]
                  and c["gross"] != float("-inf")]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["gross"])
    return best if best["gross"] > 0 else None