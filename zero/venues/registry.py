from __future__ import annotations

from .camelot_v3 import CamelotV3Adapter
from .sushi_v3 import SushiV3Adapter
from .uniswap_v3 import UniswapV3Adapter


ADAPTER_TYPES = {
    "uniswap_v3": UniswapV3Adapter,
    "sushi_v3": SushiV3Adapter,
    "camelot_v3": CamelotV3Adapter,
}


def build_venue_registry(config: dict, rpc, *, rpc_factory=None) -> dict[str, object]:
    venues = config.get("venues", {}) or {}
    registry = {}
    for venue_id, row in venues.items():
        if not bool(row.get("enabled", False)):
            continue
        adapter_type = ADAPTER_TYPES.get(venue_id)
        if adapter_type is None:
            raise ValueError(f"unsupported venue: {venue_id}")
        venue_rpc = rpc_factory(venue_id, row) if rpc_factory else rpc
        registry[venue_id] = adapter_type(
            venue_id=venue_id,
            rpc=venue_rpc,
            factory=str(row["factory"]),
            router=str(row["router"]),
            quoter=(str(row["quoter"]) if row.get("quoter") else None),
            fee_tiers=tuple(int(v) for v in row.get("fee_tiers", [])),
            exact_quote_supported=bool(row.get("exact_quote_supported", False)),
            execution_supported=bool(row.get("execution_supported", False)),
            family=str(row.get("family", venue_id)),
        )
    return registry
