from __future__ import annotations

from .calldata import (
    ExecutionStep,
    MSG_SENDER,
    SWAP_ROUTER_02,
    _exact_input_single,
    _join_words,
)
from .rpc import encode_address, encode_uint


def build_liquidation_steps(candidate: dict, aave_pool: str, router: str,
                            slippage_bps: int = 20) -> list[ExecutionStep]:
    if candidate.get('route_kind') != 'liquidation_exact':
        raise ValueError('liquidation candidate route_kind is required')
    if not bool(candidate.get('executable', False)):
        raise ValueError('liquidation candidate is not executable')
    if router.lower() != SWAP_ROUTER_02.lower():
        raise ValueError('only canonical Arbitrum SwapRouter02 is allowed')
    if not (0 <= int(slippage_bps) <= 10_000):
        raise ValueError('slippage_bps must be between 0 and 10000')

    legs = candidate.get('legs') or []
    if len(legs) != 1:
        raise ValueError('v0.5.7 liquidation execution requires one unwind leg')
    leg = legs[0]
    pool = leg.get('pool') or {}
    if pool.get('venue_id') != 'uniswap_v3':
        raise ValueError('v0.5.7 liquidation execution requires Uniswap V3')

    debt = str(candidate['base_asset'])
    collateral = str(candidate['collateral_asset'])
    borrower = str(candidate['borrower'])
    debt_to_cover = int(candidate['debt_to_cover_raw'])
    collateral_in = int(candidate['collateral_received_raw'])
    flash_fee = int(candidate['flash_fee_raw'])
    min_profit = int(candidate['min_profit_raw'])
    unwind_out = int(candidate['unwind_out_raw'])
    fee = int(pool['fee_tier'])
    required_final = debt_to_cover + flash_fee + min_profit
    if unwind_out < required_final:
        raise ValueError('liquidation unwind cannot cover repayment and minimum profit')

    approve_debt = _join_words('approve(address,uint256)', [
        encode_address(aave_pool), encode_uint(debt_to_cover)])
    liquidation = _join_words(
        'liquidationCall(address,address,address,uint256,bool)', [
            encode_address(collateral), encode_address(debt),
            encode_address(borrower), encode_uint(debt_to_cover), encode_uint(0)])
    approve_collateral = _join_words('approve(address,uint256)', [
        encode_address(router), encode_uint(collateral_in)])
    min_swap_out = max(
        required_final,
        unwind_out * (10_000 - int(slippage_bps)) // 10_000,
    )
    swap = _exact_input_single(
        token_in=collateral,
        token_out=debt,
        fee=fee,
        recipient=MSG_SENDER,
        amount_in=collateral_in,
        amount_out_minimum=min_swap_out,
    )
    return [
        ExecutionStep(debt, 0, approve_debt),
        ExecutionStep(aave_pool, 0, liquidation),
        ExecutionStep(collateral, 0, approve_collateral),
        ExecutionStep(router, 0, swap),
    ]
