from __future__ import annotations

from ..keccak import selector_hex
from ..rpc import encode_address, encode_uint
from .base import PoolRef, VenueAdapter


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
        selector = selector_hex("getPool(address,address,uint24)")
        calls = [(
            self.factory,
            selector + encode_address(token_a)[2:]
            + encode_address(token_b)[2:] + encode_uint(fee)[2:],
        ) for fee in self.fee_tiers]
        replies = self.rpc.batch_eth_call(calls, block=int(block))
        first, second = sorted((token_a.lower(), token_b.lower()),
                               key=lambda value: int(value, 16))
        pools = []
        for fee, raw in zip(self.fee_tiers, replies):
            address = _address_from_word(raw)
            if address:
                pools.append(PoolRef(self.venue_id, address, first, second,
                                     int(fee), "uniswap_v3"))
        return pools
