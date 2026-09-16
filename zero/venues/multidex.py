from __future__ import annotations

from dataclasses import dataclass

from ..keccak import keccak256
from .base import PoolRef


@dataclass(frozen=True)
class TwoLegRoute:
    chain_id: int
    block: int
    base: str
    quote: str
    leg1: PoolRef
    leg2: PoolRef

    @property
    def canonical(self) -> str:
        return "|".join([
            str(self.chain_id), self.base.lower(), self.quote.lower(),
            self.leg1.id, self.leg2.id, "base-to-quote-to-base",
        ])

    @property
    def id(self) -> str:
        return "0x" + keccak256(self.canonical.encode()).hex()


def build_two_leg_routes(chain_id: int, base: str, quote: str,
                         pools: list[PoolRef], *, block: int) -> list[TwoLegRoute]:
    routes = []
    for first in pools:
        for second in pools:
            if first.id == second.id:
                continue
            routes.append(TwoLegRoute(int(chain_id), int(block),
                                      base.lower(), quote.lower(), first, second))
    return routes


def discover_route_configs(venues: dict[str, object], pair: tuple[str, str],
                           tokens: dict[str, object], chain_id: int,
                           block: int) -> list[dict]:
    base_symbol, quote_symbol = pair
    base = tokens.get(base_symbol)
    quote = tokens.get(quote_symbol)
    if base is None or quote is None:
        return []
    pools: list[PoolRef] = []
    for adapter in venues.values():
        if not getattr(adapter, "exact_quote_supported", False):
            continue
        pools.extend(adapter.discover_pair(base.address, quote.address, int(block)))
    rows = []
    for route in build_two_leg_routes(
            int(chain_id), base.address, quote.address, pools, block=int(block)):
        if route.leg1.venue_id == "uniswap_v3" and route.leg2.venue_id == "uniswap_v3":
            continue
        rows.append({
            "block": int(block), "route_kind": "multidex_exact",
            "route_id": route.id,
            "name": f"{base_symbol}:{route.leg1.venue_id} -> {quote_symbol}:{route.leg2.venue_id}",
            "base_symbol": base_symbol, "quote_symbol": quote_symbol,
            "base": base.address, "quote": quote.address,
            "base_decimals": int(base.decimals), "quote_decimals": int(quote.decimals),
            "base_price_usd": float(base.price_usd), "quote_price_usd": float(quote.price_usd),
            "leg1": route.leg1.__dict__.copy(), "leg2": route.leg2.__dict__.copy(),
        })
    return rows
