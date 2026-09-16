from __future__ import annotations

from dataclasses import dataclass

from .keccak import keccak256
from .venues.base import PoolRef


@dataclass(frozen=True)
class RouteLeg:
    pool: PoolRef
    token_in: str
    token_out: str

    def __post_init__(self) -> None:
        token_in = self.token_in.lower()
        token_out = self.token_out.lower()
        members = {self.pool.token0.lower(), self.pool.token1.lower()}
        if token_in == token_out or {token_in, token_out} != members:
            raise ValueError("route leg must traverse exactly one pool pair")
        object.__setattr__(self, "token_in", token_in)
        object.__setattr__(self, "token_out", token_out)

    @property
    def canonical(self) -> str:
        return f"{self.pool.id}|{self.token_in}|{self.token_out}"


@dataclass(frozen=True)
class RouteCandidate:
    chain_id: int
    start_asset: str
    legs: tuple[RouteLeg, ...]

    def __post_init__(self) -> None:
        start = self.start_asset.lower()
        if not 2 <= len(self.legs) <= 3:
            raise ValueError("routes require two or three legs")
        if self.legs[0].token_in != start or self.legs[-1].token_out != start:
            raise ValueError("route must start and end in the flash asset")
        for left, right in zip(self.legs, self.legs[1:]):
            if left.token_out != right.token_in:
                raise ValueError("route legs are not contiguous")
        if len({leg.pool.id for leg in self.legs}) != len(self.legs):
            raise ValueError("route may not reuse a pool")
        object.__setattr__(self, "start_asset", start)

    @property
    def canonical(self) -> str:
        return "|".join([
            str(self.chain_id), self.start_asset,
            *(leg.canonical for leg in self.legs),
        ])

    @property
    def id(self) -> str:
        return "0x" + keccak256(self.canonical.encode()).hex()


class RouteGraph:
    def __init__(self, chain_id: int, pools: list[PoolRef]):
        self.chain_id = int(chain_id)
        self.pools = tuple(sorted(pools, key=lambda pool: pool.id))
        self._edges: dict[str, list[RouteLeg]] = {}
        for pool in self.pools:
            token0 = pool.token0.lower()
            token1 = pool.token1.lower()
            self._edges.setdefault(token0, []).append(
                RouteLeg(pool, token0, token1))
            self._edges.setdefault(token1, []).append(
                RouteLeg(pool, token1, token0))
        for token in self._edges:
            self._edges[token].sort(
                key=lambda leg: (leg.pool.id, leg.token_out))

    def enumerate_cycles(self, start_asset: str,
                         max_hops: int = 3) -> list[RouteCandidate]:
        max_hops = int(max_hops)
        if max_hops < 2 or max_hops > 3:
            raise ValueError("max_hops must be between 2 and 3")
        start = start_asset.lower()
        found: dict[str, RouteCandidate] = {}

        def walk(current: str, legs: tuple[RouteLeg, ...],
                 used_pools: frozenset[str],
                 visited_tokens: frozenset[str]) -> None:
            if len(legs) >= max_hops:
                return
            for leg in self._edges.get(current, ()):
                if leg.pool.id in used_pools:
                    continue
                target = leg.token_out
                next_legs = legs + (leg,)
                if target == start:
                    if len(next_legs) >= 2:
                        route = RouteCandidate(
                            self.chain_id, start, next_legs)
                        found[route.id] = route
                    continue
                if target in visited_tokens:
                    continue
                walk(target, next_legs,
                     used_pools | {leg.pool.id},
                     visited_tokens | {target})

        walk(start, tuple(), frozenset(), frozenset({start}))
        return [found[key] for key in sorted(found)]


def build_multihop_route_configs(chain_id: int, pair: tuple[str, str],
                                 tokens: dict[str, object],
                                 pools: list[PoolRef], *, block: int,
                                 max_routes: int = 24) -> list[dict]:
    max_routes = int(max_routes)
    if max_routes <= 0:
        return []
    base_symbol, quote_symbol = pair
    base = tokens.get(base_symbol)
    quote = tokens.get(quote_symbol)
    if base is None or quote is None:
        return []
    graph = RouteGraph(int(chain_id), pools)
    cycles = graph.enumerate_cycles(base.address, max_hops=3)
    rows = []
    quote_address = quote.address.lower()
    for route in cycles:
        if len(route.legs) != 3:
            continue
        traversed = {leg.token_in for leg in route.legs}
        traversed.update(leg.token_out for leg in route.legs)
        if quote_address not in traversed:
            continue
        rows.append({
            "block": int(block),
            "route_kind": "multihop_exact",
            "route_id": route.id,
            "name": f"{base_symbol} 3-hop cycle via {quote_symbol}",
            "base_symbol": base_symbol,
            "quote_symbol": quote_symbol,
            "base": base.address,
            "quote": quote.address,
            "base_decimals": int(base.decimals),
            "quote_decimals": int(quote.decimals),
            "base_price_usd": float(base.price_usd),
            "quote_price_usd": float(quote.price_usd),
            "executable": False,
            "legs": [
                {
                    "pool": leg.pool.__dict__.copy(),
                    "token_in": leg.token_in,
                    "token_out": leg.token_out,
                }
                for leg in route.legs
            ],
        })
        if len(rows) >= max_routes:
            break
    return rows
