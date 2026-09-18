from __future__ import annotations

from ..keccak import selector_hex
from ..rpc import encode_address, encode_uint
from ..uniswap_quoter import UniswapV3Quoter
from .base import PoolRef, VenueAdapter, VenueQuote


def _address_from_word(raw: bytes) -> str | None:
    if not raw:
        return None
    value = int.from_bytes(raw[:32], "big")
    if value == 0:
        return None
    return "0x" + value.to_bytes(20, "big").hex()


class UniswapV3Adapter(VenueAdapter):
    """Uniswap V3-compatible factory discovery at an explicit block."""

    def discover_pair(self, token_a: str, token_b: str,
                      block: int) -> list[PoolRef]:
        return self.discover_pairs(
            [(token_a, token_b)], int(block)).get((token_a, token_b), [])

    def discover_pairs(self, pairs: list[tuple[str, str]], block: int, *,
                       max_batch: int = 100) -> dict[tuple[str, str], list[PoolRef]]:
        selector = selector_hex("getPool(address,address,uint24)")
        calls = []
        metadata = []
        for pair in pairs:
            token_a, token_b = pair
            for fee in self.fee_tiers:
                calls.append((
                    self.factory,
                    selector + encode_address(token_a)[2:]
                    + encode_address(token_b)[2:] + encode_uint(fee)[2:],
                ))
                metadata.append((pair, int(fee)))
        found = {pair: [] for pair in pairs}
        if not calls:
            return found
        batch_results = getattr(self.rpc, "batch_eth_call_results", None)
        if callable(batch_results):
            replies = batch_results(calls, block=int(block), max_batch=int(max_batch))
        else:
            replies = self.rpc.batch_eth_call(
                calls, block=int(block), max_batch=int(max_batch))
        for (pair, fee), raw in zip(metadata, replies):
            if isinstance(raw, Exception):
                continue
            address = _address_from_word(raw)
            if not address:
                continue
            token_a, token_b = pair
            first, second = sorted((token_a.lower(), token_b.lower()),
                                   key=lambda value: int(value, 16))
            found[pair].append(PoolRef(
                self.venue_id, address, first, second, fee, "uniswap_v3"))
        return found

    def quote_exact_input(self, pool: PoolRef, token_in: str,
                          amount_in: int, block: int) -> VenueQuote:
        if not self.exact_quote_supported or not self.quoter:
            raise ValueError(f"exact quotes disabled for {self.venue_id}")
        if pool.fee_tier is None:
            raise ValueError("V3 quote requires fee tier")
        token_in = token_in.lower()
        if token_in == pool.token0.lower():
            token_out = pool.token1
        elif token_in == pool.token1.lower():
            token_out = pool.token0
        else:
            raise ValueError("token_in is not in pool")
        result = UniswapV3Quoter(self.rpc, self.quoter).quote_exact_input_single(
            token_in=token_in, token_out=token_out, fee=int(pool.fee_tier),
            amount_in=int(amount_in), block=int(block))
        return VenueQuote(amount_out=int(result.amount_out),
                          gas_estimate=int(result.gas_estimate),
                          fee_used=int(pool.fee_tier))
