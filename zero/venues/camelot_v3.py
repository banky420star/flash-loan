from __future__ import annotations

from ..keccak import selector_hex
from ..rpc import encode_address
from .base import PoolRef, VenueAdapter
from .uniswap_v3 import _address_from_word


class CamelotV3Adapter(VenueAdapter):
    """Camelot Algebra V3 discovery at an explicit block."""

    def discover_pair(self, token_a: str, token_b: str,
                      block: int) -> list[PoolRef]:
        data = (selector_hex("poolByPair(address,address)")
                + encode_address(token_a)[2:]
                + encode_address(token_b)[2:])
        raw = self.rpc.eth_call(self.factory, data, block=int(block))
        address = _address_from_word(raw)
        if not address:
            return []
        first, second = sorted((token_a.lower(), token_b.lower()),
                               key=lambda value: int(value, 16))
        return [PoolRef(self.venue_id, address, first, second,
                        None, "algebra_v3")]
