"""Batched pinned-block market-data helpers for the ZERO swarm."""

from __future__ import annotations

from .keccak import selector_hex
from .rpc import decode_uints, encode_address
from .swarm import TokenInfo


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
    """Resolve Aave reserve metadata/prices in bounded calls at one block."""
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

    replies = engine.rpc.batch_eth_call(
        calls, block=block, max_batch=max_batch)
    if len(replies) != len(calls):
        raise ValueError("incomplete batched token metadata reply")

    registry: dict[str, TokenInfo] = {}
    for index, token in enumerate(reserves):
        raw_symbol, raw_decimals, raw_price = replies[index * 3:index * 3 + 3]
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
