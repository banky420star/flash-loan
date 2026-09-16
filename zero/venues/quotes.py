from __future__ import annotations

from .base import PoolRef, VenueQuote
from .multidex import TwoLegRoute


def quote_leg(registry: dict[str, object], pool: PoolRef,
              amount_in: int, token_in: str, block: int) -> VenueQuote:
    if amount_in <= 0:
        raise ValueError("amount_in must be positive")
    adapter = registry.get(pool.venue_id)
    if adapter is None:
        raise ValueError(f"unknown venue: {pool.venue_id}")
    if not adapter.exact_quote_supported:
        raise ValueError(f"exact quotes disabled for {pool.venue_id}")
    return adapter.quote_exact_input(pool, token_in, int(amount_in), int(block))


def route_execution_ready(route: TwoLegRoute,
                          registry: dict[str, object]) -> bool:
    for pool in (route.leg1, route.leg2):
        adapter = registry.get(pool.venue_id)
        if adapter is None:
            return False
        if not adapter.exact_quote_supported or not adapter.execution_supported:
            return False
    return True
