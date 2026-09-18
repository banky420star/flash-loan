"""Pure static ABI encoding for the v0.4 Uniswap V3 fork bridge."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .candidate import ArbitrageCandidate
from .keccak import selector_hex
from .rpc import encode_address, encode_uint


SWAP_ROUTER_02 = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
# Sushi's Arbitrum V3 router is the classic periphery SwapRouter: same
# pool interface as SwapRouter02 but its params carry a deadline.
SUSHI_V3_ROUTER = "0x8A21F6768C1f8075791D08546Dadf6daA0bE820c"
MSG_SENDER = "0x0000000000000000000000000000000000000001"
ADDRESS_THIS = "0x0000000000000000000000000000000000000002"
EXACT_INPUT_SINGLE = "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))"
EXACT_INPUT_SINGLE_DEADLINE = "exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"


@dataclass(frozen=True)
class ExecutionStep:
    target: str
    value: int
    data: str

    def as_dict(self) -> dict:
        return asdict(self)


def _join_words(signature: str, words: list[str]) -> str:
    return selector_hex(signature) + "".join(word.removeprefix("0x") for word in words)


def _exact_input_single(*, token_in: str, token_out: str, fee: int,
                        recipient: str, amount_in: int,
                        amount_out_minimum: int,
                        deadline: int | None = None) -> str:
    if not (0 <= fee < 2 ** 24):
        raise ValueError("fee must fit uint24")
    if amount_in < 0 or amount_out_minimum < 0:
        raise ValueError("swap amounts must be non-negative")
    if deadline is None:
        signature = EXACT_INPUT_SINGLE
        extra: list[str] = []
    else:
        signature = EXACT_INPUT_SINGLE_DEADLINE
        extra = [encode_uint(deadline)]
    return _join_words(signature, [
        encode_address(token_in),
        encode_address(token_out),
        encode_uint(fee),
        encode_address(recipient),
        *extra,
        encode_uint(amount_in),
        encode_uint(amount_out_minimum),
        encode_uint(0),
    ])


def build_uniswap_v3_steps(candidate: ArbitrageCandidate, router: str,
                            slippage_bps: int = 20) -> list[ExecutionStep]:
    if not (0 <= slippage_bps <= 10_000):
        raise ValueError("slippage_bps must be between 0 and 10000")
    if router.lower() != SWAP_ROUTER_02.lower():
        raise ValueError("only canonical Arbitrum SwapRouter02 is allowed")

    first_min = candidate.hop1_expected_out_raw * (10_000 - slippage_bps) // 10_000
    required_final = (
        candidate.loan_amount_raw
        + candidate.flash_fee_raw
        + candidate.min_profit_raw
    )
    if candidate.hop2_expected_out_raw < required_final:
        raise ValueError("candidate cannot cover principal, flash fee, and minimum profit")

    approve = _join_words("approve(address,uint256)", [
        encode_address(router),
        encode_uint(candidate.loan_amount_raw),
    ])
    leg1 = _exact_input_single(
        token_in=candidate.base_asset,
        token_out=candidate.quote_asset,
        fee=candidate.fee1,
        recipient=ADDRESS_THIS,
        amount_in=candidate.loan_amount_raw,
        amount_out_minimum=first_min,
    )
    leg2 = _exact_input_single(
        token_in=candidate.quote_asset,
        token_out=candidate.base_asset,
        fee=candidate.fee2,
        recipient=MSG_SENDER,
        amount_in=0,
        amount_out_minimum=required_final,
    )
    return [
        ExecutionStep(candidate.base_asset, 0, approve),
        ExecutionStep(router, 0, leg1),
        ExecutionStep(router, 0, leg2),
    ]
