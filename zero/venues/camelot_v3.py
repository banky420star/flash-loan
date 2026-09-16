from __future__ import annotations

from ..keccak import selector_hex
from ..rpc import decode_uints, encode_address, encode_uint
from .base import PoolRef, VenueAdapter, VenueQuote
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

    def quote_exact_input(self, pool: PoolRef, token_in: str,
                          amount_in: int, block: int) -> VenueQuote:
        if not self.exact_quote_supported or not self.quoter:
            raise ValueError("Camelot exact quote support is disabled")
        token_in = token_in.lower()
        if token_in == pool.token0.lower():
            token_out = pool.token1
        elif token_in == pool.token1.lower():
            token_out = pool.token0
        else:
            raise ValueError("token_in is not in pool")
        data = selector_hex(
            "quoteExactInputSingle(address,address,uint256,uint160)")
        data += encode_address(token_in)[2:] + encode_address(token_out)[2:]
        data += encode_uint(int(amount_in))[2:] + encode_uint(0)[2:]
        words = decode_uints(self.rpc.eth_call(self.quoter, data, block=int(block)))
        if len(words) < 2:
            raise ValueError("Camelot quoter returned incomplete result")
        return VenueQuote(amount_out=int(words[0]), fee_used=int(words[1]))
