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


def discover_route_configs_many(venues: dict[str, object],
                                pairs: list[tuple[str, str]],
                                tokens: dict[str, object], chain_id: int,
                                block: int, *, max_batch: int = 100,
                                max_workers: int = 3,
                                timeout_s: float = 5.0,
                                max_routes_per_pair: int = 0,
                                seed_pools_by_pair: dict[tuple[str, str], list[PoolRef]] | None = None
                                ) -> tuple[list[dict], list[dict]]:
    """Discover all enabled venues across many symbol pairs concurrently.

    Each venue receives the full address-pair set so adapters can batch their
    factory reads. A slow venue is isolated by the caller-supplied timeout and
    does not block routes discovered by other venues.
    """
    from concurrent.futures import ThreadPoolExecutor, wait

    resolved: list[tuple[tuple[str, str], tuple[str, str], object, object]] = []
    for pair in pairs:
        base_symbol, quote_symbol = pair
        base = tokens.get(base_symbol)
        quote = tokens.get(quote_symbol)
        if base is None or quote is None:
            continue
        address_pair = (base.address.lower(), quote.address.lower())
        resolved.append((pair, address_pair, base, quote))
    if not resolved:
        return [], []

    address_pairs = [item[1] for item in resolved]
    workers = max(1, min(int(max_workers), len(venues)))
    timeout = max(0.01, float(timeout_s))
    executor = ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="zero-venue-discovery")
    futures = {}
    for venue_id, adapter in venues.items():
        if not getattr(adapter, "exact_quote_supported", False):
            continue
        futures[executor.submit(
            adapter.discover_pairs, address_pairs, int(block),
            max_batch=int(max_batch))] = venue_id

    done, pending = wait(futures, timeout=timeout) if futures else (set(), set())
    seeds = seed_pools_by_pair or {}
    by_pair: dict[tuple[str, str], list[PoolRef]] = {
        item[1]: list(seeds.get(item[1], [])) for item in resolved
    }
    errors: list[dict] = []
    for future in done:
        venue_id = futures[future]
        try:
            result = future.result()
        except Exception as exc:
            errors.append({"venue_id": venue_id,
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        for address_pair, pools in (result or {}).items():
            if address_pair in by_pair:
                by_pair[address_pair].extend(list(pools))
    for future in pending:
        venue_id = futures[future]
        future.cancel()
        errors.append({"venue_id": venue_id, "error": "timeout"})
    executor.shutdown(wait=False, cancel_futures=True)

    rows: list[dict] = []
    for _, address_pair, base, quote in resolved:
        unique = {pool.canonical: pool for pool in by_pair.get(address_pair, [])}
        pools = [unique[key] for key in sorted(unique)]
        pair_routes = [
            route for route in build_two_leg_routes(
                int(chain_id), base.address, quote.address, pools,
                block=int(block))
            if not (route.leg1.venue_id == "uniswap_v3"
                    and route.leg2.venue_id == "uniswap_v3")
        ]
        pair_routes.sort(key=lambda route: (
            route.leg1.venue_id == route.leg2.venue_id,
            int(route.leg1.fee_tier or 0) + int(route.leg2.fee_tier or 0),
            route.leg1.canonical, route.leg2.canonical,
        ))
        route_cap = max(0, int(max_routes_per_pair))
        if route_cap and len(pair_routes) > route_cap:
            start = int(block) % len(pair_routes)
            pair_routes = [
                pair_routes[(start + offset) % len(pair_routes)]
                for offset in range(route_cap)
            ]
        for route in pair_routes:
            rows.append({
                "block": int(block), "route_kind": "multidex_exact",
                "route_id": route.id,
                "name": (f"{base.symbol}:{route.leg1.venue_id} -> "
                         f"{quote.symbol}:{route.leg2.venue_id}"),
                "base_symbol": base.symbol, "quote_symbol": quote.symbol,
                "base": base.address, "quote": quote.address,
                "base_decimals": int(base.decimals),
                "quote_decimals": int(quote.decimals),
                "base_price_usd": float(base.price_usd),
                "quote_price_usd": float(quote.price_usd),
                "leg1": route.leg1.__dict__.copy(),
                "leg2": route.leg2.__dict__.copy(),
            })
    errors.sort(key=lambda row: row["venue_id"])
    return rows, errors
