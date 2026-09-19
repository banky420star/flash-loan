"""Batched pinned-block market-data helpers for the ZERO swarm."""

from __future__ import annotations

from .keccak import selector_hex
from .rpc import RpcError, decode_uints, encode_address, encode_uint
from .swarm import RouteKey, ScanContext, TokenInfo, _pool_config


def _decode_uint(raw: bytes) -> int:
    words = decode_uints(raw)
    if not words:
        raise ValueError("empty uint reply")
    return words[0]


def _decode_symbol(raw: bytes) -> str:
    words = decode_uints(raw)
    if not words:
        raise ValueError("empty symbol reply")
    start = words[0] // 32
    if start >= len(words):
        raise ValueError("bad symbol offset")
    length = words[start]
    chunk = b"".join(word.to_bytes(32, "big") for word in words[start + 1:])
    if length > len(chunk):
        raise ValueError("bad symbol length")
    return chunk[:length].decode()


def build_token_registry_batched(engine, block: int, *,
                                 pool: str | None = None,
                                 oracle: str | None = None,
                                 max_batch: int = 100) -> dict[str, TokenInfo]:
    """Resolve reserve metadata while isolating per-token member errors."""
    block = int(block)
    pool = pool or engine.aave.pool_address(block=block)
    oracle = oracle or engine.aave.oracle_address(block=block)
    reserves = list(engine.aave.reserves_list(pool, block=block))

    symbol_selector = selector_hex("symbol()")
    decimals_selector = selector_hex("decimals()")
    price_selector = selector_hex("getAssetPrice(address)")

    calls: list[tuple[str, str]] = []
    for token in reserves:
        calls.extend([
            (token, symbol_selector),
            (token, decimals_selector),
            (oracle, price_selector + encode_address(token)[2:]),
        ])

    tolerant = getattr(engine.rpc, "batch_eth_call_results", None)
    if tolerant is None:
        replies = engine.rpc.batch_eth_call(
            calls, block=block, max_batch=max_batch)
    else:
        replies = tolerant(calls, block=block, max_batch=max_batch)
    if len(replies) != len(calls):
        raise ValueError("incomplete batched token metadata reply")

    registry: dict[str, TokenInfo] = {}
    for index, token in enumerate(reserves):
        triple = replies[index * 3:index * 3 + 3]
        if any(isinstance(value, RpcError) for value in triple):
            continue
        raw_symbol, raw_decimals, raw_price = triple
        try:
            symbol = _decode_symbol(raw_symbol)
            decimals = int(_decode_uint(raw_decimals))
            price_usd = _decode_uint(raw_price) / 1e8
            if not symbol or decimals < 0 or price_usd <= 0:
                raise ValueError("invalid token metadata")
        except Exception:
            # Skip a malformed reserve row, but never retry it outside this
            # pinned batch at a different block.
            continue
        registry[symbol] = TokenInfo(
            symbol=symbol,
            address=str(token).lower(),
            decimals=decimals,
            price_usd=float(price_usd),
        )
    return registry


def refresh_token_prices_batched(engine, block: int,
                                 tokens: dict[str, TokenInfo], *,
                                 oracle: str,
                                 max_batch: int = 100) -> dict[str, TokenInfo]:
    """Refresh only oracle prices for cached static token metadata."""
    block = int(block)
    price_selector = selector_hex("getAssetPrice(address)")
    items = list(tokens.items())
    calls = [
        (oracle, price_selector + encode_address(token.address)[2:])
        for _, token in items
    ]
    if not calls:
        return {}
    tolerant = getattr(engine.rpc, "batch_eth_call_results", None)
    if tolerant is None:
        replies = engine.rpc.batch_eth_call(
            calls, block=block, max_batch=max_batch)
    else:
        replies = tolerant(calls, block=block, max_batch=max_batch)
    if len(replies) != len(calls):
        raise ValueError("incomplete batched token price reply")

    refreshed: dict[str, TokenInfo] = {}
    for (symbol, token), raw in zip(items, replies):
        if isinstance(raw, RpcError):
            continue
        try:
            price_usd = _decode_uint(raw) / 1e8
            if price_usd <= 0:
                raise ValueError("invalid token price")
        except Exception:
            continue
        refreshed[symbol] = TokenInfo(
            symbol=token.symbol,
            address=token.address,
            decimals=int(token.decimals),
            price_usd=float(price_usd),
        )
    return refreshed


def discover_uniswap_routes_batched(engine, pair: tuple[str, str],
                                    registry: dict[str, TokenInfo], block: int,
                                    fee_tiers: list[int], *,
                                    max_batch: int = 100) -> list[dict]:
    """Discover ordered V3 two-pool cycles with one pinned factory batch."""
    block = int(block)
    base_symbol, quote_symbol = pair
    base = registry.get(base_symbol)
    quote = registry.get(quote_symbol)
    if base is None or quote is None:
        return []

    factory = engine.config["uniswap_v3_factory"]
    selector = selector_hex("getPool(address,address,uint24)")
    calls = [
        (
            factory,
            selector
            + encode_address(base.address)[2:]
            + encode_address(quote.address)[2:]
            + encode_uint(int(fee))[2:],
        )
        for fee in fee_tiers
    ]
    replies = engine.rpc.batch_eth_call(
        calls, block=block, max_batch=max_batch)
    if len(replies) != len(calls):
        raise ValueError("incomplete batched pool discovery reply")

    pools: list[dict] = []
    for fee, raw in zip(fee_tiers, replies):
        if not raw:
            continue
        pool_int = int.from_bytes(raw[:32], "big")
        if pool_int == 0:
            continue
        pool_address = "0x" + pool_int.to_bytes(20, "big").hex()
        pools.append(_pool_config(base, quote, pool_address, int(fee)))

    routes: list[dict] = []
    for i, pool_a in enumerate(pools):
        for j, pool_b in enumerate(pools):
            if i == j:
                continue
            key = RouteKey(
                chain_id=int(engine.config["chain_id"]),
                base=base.address,
                quote=quote.address,
                pool_a=pool_a["address"],
                pool_b=pool_b["address"],
                fee_a=pool_a["fee_tier"],
                fee_b=pool_b["fee_tier"],
                direction="base-to-quote-to-base",
            )
            routes.append({
                "block": block,
                "route_id": key.id,
                "name": (
                    f"{base_symbol}->{quote_symbol} {pool_a['fee_percent']}% | "
                    f"{quote_symbol}->{base_symbol} {pool_b['fee_percent']}%"
                ),
                "base_symbol": base_symbol,
                "quote_symbol": quote_symbol,
                "base": base.address,
                "quote": quote.address,
                "base_decimals": base.decimals,
                "quote_decimals": quote.decimals,
                "base_price_usd": base.price_usd,
                "quote_price_usd": quote.price_usd,
                "pools": [pool_a, pool_b],
            })
    return routes


def discover_uniswap_routes_many_batched(
        engine, pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...],
        registry: dict[str, TokenInfo], block: int, fee_tiers: list[int], *,
        max_batch: int = 100) -> list[dict]:
    """Discover V3 cycles for many pairs with one pinned factory batch."""
    block = int(block)
    factory = engine.config["uniswap_v3_factory"]
    selector = selector_hex("getPool(address,address,uint24)")
    ordered_pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    metadata: list[tuple[tuple[str, str], TokenInfo, TokenInfo, int]] = []
    calls: list[tuple[str, str]] = []

    for raw_pair in pairs:
        pair = (str(raw_pair[0]), str(raw_pair[1]))
        if pair in seen:
            continue
        seen.add(pair)
        base = registry.get(pair[0])
        quote = registry.get(pair[1])
        if base is None or quote is None:
            continue
        ordered_pairs.append(pair)
        for raw_fee in fee_tiers:
            fee = int(raw_fee)
            data = (
                selector
                + encode_address(base.address)[2:]
                + encode_address(quote.address)[2:]
                + encode_uint(fee)[2:]
            )
            calls.append((factory, data))
            metadata.append((pair, base, quote, fee))

    if not calls:
        return []
    replies = engine.rpc.batch_eth_call(
        calls, block=block, max_batch=max_batch)
    if len(replies) != len(calls):
        raise ValueError("incomplete batched multi-pair pool discovery reply")

    pools_by_pair: dict[tuple[str, str], list[dict]] = {
        pair: [] for pair in ordered_pairs
    }
    tokens_by_pair: dict[tuple[str, str], tuple[TokenInfo, TokenInfo]] = {}
    for (pair, base, quote, fee), raw in zip(metadata, replies):
        tokens_by_pair[pair] = (base, quote)
        if not raw:
            continue
        pool_int = int.from_bytes(raw[:32], "big")
        if pool_int == 0:
            continue
        pool_address = "0x" + pool_int.to_bytes(20, "big").hex()
        pools_by_pair[pair].append(
            _pool_config(base, quote, pool_address, fee))

    routes: list[dict] = []
    for pair in ordered_pairs:
        base, quote = tokens_by_pair[pair]
        base_symbol, quote_symbol = pair
        pools = pools_by_pair[pair]
        for i, pool_a in enumerate(pools):
            for j, pool_b in enumerate(pools):
                if i == j:
                    continue
                key = RouteKey(
                    chain_id=int(engine.config["chain_id"]),
                    base=base.address, quote=quote.address,
                    pool_a=pool_a["address"], pool_b=pool_b["address"],
                    fee_a=pool_a["fee_tier"], fee_b=pool_b["fee_tier"],
                    direction="base-to-quote-to-base",
                )
                routes.append({
                    "block": block, "route_id": key.id,
                    "name": (
                        f"{base_symbol}->{quote_symbol} {pool_a['fee_percent']}% | "
                        f"{quote_symbol}->{base_symbol} {pool_b['fee_percent']}%"
                    ),
                    "base_symbol": base_symbol, "quote_symbol": quote_symbol,
                    "base": base.address, "quote": quote.address,
                    "base_decimals": base.decimals,
                    "quote_decimals": quote.decimals,
                    "base_price_usd": base.price_usd,
                    "quote_price_usd": quote.price_usd,
                    "pools": [pool_a, pool_b],
                })
    return routes


def build_scan_context_batched(engine, block: int,
                               route_catalog: list[dict], *,
                               tokens: dict[str, TokenInfo] | None = None,
                               aave_pool: str | None = None,
                               oracle: str | None = None,
                               max_batch: int = 100) -> ScanContext:
    """Freeze Aave + deduplicated V3 pool state using pinned batches."""
    block = int(block)
    for route in route_catalog:
        if int(route.get("block", block)) != block:
            raise ValueError("mixed-block scan context")

    aave_pool = aave_pool or engine.aave.pool_address(block=block)
    oracle = oracle or engine.aave.oracle_address(block=block)
    # Executor's live path is runBalancer (Balancer vault, 0% premium,
    # fork-tested) — model the trade we would actually fire, not Aave's.
    premium_bps = 0
    if tokens is None:
        tokens = build_token_registry_batched(
            engine, block, pool=aave_pool, oracle=oracle,
            max_batch=max_batch)

    eth_asset = str(engine.config["arbitrage"]["eth_for_gas"]).lower()
    eth_info = next(
        (token for token in tokens.values()
         if token.address.lower() == eth_asset),
        None,
    )
    if eth_info is not None:
        eth_price_usd = float(eth_info.price_usd)
    else:
        # Same pinned block fallback only; never read latest inside a cycle.
        eth_price_usd = float(engine.aave.asset_price(
            oracle, eth_asset, block=block))
    gas_usd = float(engine._gas_cost_usd(eth_price_usd))

    pool_addresses: list[str] = []
    seen: set[str] = set()
    for route in route_catalog:
        for pool_cfg in route.get("pools", []):
            address = str(pool_cfg.get("address", "")).lower()
            if not address or address in seen:
                continue
            seen.add(address)
            pool_addresses.append(address)

    slot0_selector = selector_hex("slot0()")
    liquidity_selector = selector_hex("liquidity()")
    calls: list[tuple[str, str]] = []
    for address in pool_addresses:
        calls.extend([
            (address, slot0_selector),
            (address, liquidity_selector),
        ])
    replies = engine.rpc.batch_eth_call(
        calls, block=block, max_batch=max_batch)
    if len(replies) != len(calls):
        raise ValueError("incomplete batched pool state reply")

    pool_states: dict[str, dict] = {}
    for index, address in enumerate(pool_addresses):
        raw_slot0, raw_liquidity = replies[index * 2:index * 2 + 2]
        pool_states[address] = {
            "sqrtPriceX96": _decode_uint(raw_slot0),
            "liquidity": _decode_uint(raw_liquidity),
        }

    return ScanContext(
        block=block,
        aave_pool=aave_pool,
        oracle=oracle,
        premium_bps=premium_bps,
        eth_price_usd=eth_price_usd,
        gas_usd=gas_usd,
        tokens=tokens,
        pool_states=pool_states,
    )
