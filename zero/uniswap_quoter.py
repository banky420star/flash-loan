"""Pinned-block Uniswap V3 QuoterV2 adapter for exact preflight checks."""

from __future__ import annotations

from dataclasses import dataclass

from .keccak import selector_hex
from .rpc import decode_uints, encode_address, encode_uint


QUOTER_V2_ARBITRUM = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
QUOTE_EXACT_INPUT_SINGLE = (
    "quoteExactInputSingle((address,address,uint256,uint24,uint160))"
)


@dataclass(frozen=True)
class QuoteResult:
    amount_out: int
    sqrt_price_x96_after: int
    initialized_ticks_crossed: int
    gas_estimate: int


class UniswapV3Quoter:
    """Use canonical QuoterV2 via eth_call at one explicit block."""

    def __init__(self, rpc, address: str = QUOTER_V2_ARBITRUM):
        self.rpc = rpc
        self.address = address

    def quote_exact_input_single(self, *, token_in: str, token_out: str,
                                 fee: int, amount_in: int,
                                 block: int | str) -> QuoteResult:
        if amount_in <= 0:
            raise ValueError("amount_in must be positive")
        if not (0 <= int(fee) < 2 ** 24):
            raise ValueError("fee must fit uint24")

        data = selector_hex(QUOTE_EXACT_INPUT_SINGLE) + "".join([
            encode_address(token_in)[2:],
            encode_address(token_out)[2:],
            encode_uint(int(amount_in))[2:],
            encode_uint(int(fee))[2:],
            encode_uint(0)[2:],
        ])
        raw = self.rpc.eth_call(self.address, data, block=block)
        words = decode_uints(raw)
        if len(words) < 4:
            raise ValueError("QuoterV2 returned incomplete result")
        return QuoteResult(
            amount_out=words[0],
            sqrt_price_x96_after=words[1],
            initialized_ticks_crossed=words[2],
            gas_estimate=words[3],
        )
