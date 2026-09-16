from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .liquidations import LiquidationState
from .routes import RouteLeg
from .venues.quotes import quote_leg


def _ceil_div(numerator: int, denominator: int) -> int:
    return (int(numerator) + int(denominator) - 1) // int(denominator)


@dataclass(frozen=True)
class UnwindRoute:
    legs: tuple[RouteLeg, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.legs) <= 3:
            raise ValueError('unwind route requires one to three legs')
        for left, right in zip(self.legs, self.legs[1:]):
            if left.token_out != right.token_in:
                raise ValueError('unwind route legs are not contiguous')
        if len({leg.pool.id for leg in self.legs}) != len(self.legs):
            raise ValueError('unwind route may not reuse a pool')

    @property
    def token_in(self) -> str:
        return self.legs[0].token_in

    @property
    def token_out(self) -> str:
        return self.legs[-1].token_out


@dataclass(frozen=True)
class LiquidationCandidate:
    block: int
    borrower: str
    debt_asset: str
    collateral_asset: str
    debt_to_cover_raw: int
    collateral_received_raw: int
    unwind_out_raw: int
    flash_fee_raw: int
    min_profit_raw: int
    gross_usd: Decimal
    flash_fee_usd: Decimal
    gas_usd: Decimal
    reserve_usd: Decimal
    expected_net_usd: Decimal
    executable: bool
    route: UnwindRoute


def _collateral_for_debt(state: LiquidationState, debt_asset: str,
                         collateral_asset: str) -> tuple[int, int]:
    debt = state.position(debt_asset)
    collateral = state.position(collateral_asset)
    debt_to_cover_raw = state.max_debt_to_cover_raw(collateral_asset, debt_asset)
    if debt_to_cover_raw <= 0 or collateral.collateral_raw <= 0:
        return 0, 0

    debt_tokens = Decimal(debt_to_cover_raw) / (Decimal(10) ** debt.decimals)
    base_collateral_tokens = debt_tokens * debt.price_usd / collateral.price_usd
    total_collateral_tokens = (
        base_collateral_tokens * Decimal(collateral.liquidation_bonus_bps)
        / Decimal(10_000))
    total_collateral_raw = int((
        total_collateral_tokens * (Decimal(10) ** collateral.decimals)
    ).to_integral_value(rounding=ROUND_FLOOR))

    if total_collateral_raw > collateral.collateral_raw:
        total_collateral_raw = int(collateral.collateral_raw)
        collateral_tokens = (
            Decimal(total_collateral_raw) / (Decimal(10) ** collateral.decimals))
        debt_needed_tokens = (
            collateral_tokens * collateral.price_usd / debt.price_usd
            * Decimal(10_000) / Decimal(collateral.liquidation_bonus_bps))
        debt_to_cover_raw = int((
            debt_needed_tokens * (Decimal(10) ** debt.decimals)
        ).to_integral_value(rounding=ROUND_CEILING))

    base_without_bonus_raw = (
        total_collateral_raw * 10_000 // collateral.liquidation_bonus_bps)
    bonus_collateral_raw = total_collateral_raw - base_without_bonus_raw
    protocol_fee_raw = _ceil_div(
        bonus_collateral_raw * collateral.liquidation_protocol_fee_bps, 10_000)
    liquidator_collateral_raw = max(0, total_collateral_raw - protocol_fee_raw)
    return debt_to_cover_raw, liquidator_collateral_raw


def quote_liquidation(state: LiquidationState, debt_asset: str,
                      collateral_asset: str, route: UnwindRoute, block: int,
                      venue_registry: dict[str, object], *,
                      flash_premium_bps: int, gas_usd: Decimal,
                      reserve_usd: Decimal) -> LiquidationCandidate | None:
    block = int(block)
    if block != int(state.block):
        raise ValueError('liquidation quote block must match state block')
    if not state.liquidatable:
        return None
    if route.token_in.lower() != collateral_asset.lower():
        raise ValueError('unwind route must start in collateral asset')
    if route.token_out.lower() != debt_asset.lower():
        raise ValueError('unwind route must end in debt asset')
    for leg in route.legs:
        adapter = venue_registry.get(leg.pool.venue_id)
        if adapter is None or not bool(getattr(adapter, 'exact_quote_supported', False)):
            return None
        if not bool(getattr(adapter, 'execution_supported', False)):
            return None

    debt_to_cover_raw, collateral_received_raw = _collateral_for_debt(
        state, debt_asset, collateral_asset)
    if debt_to_cover_raw <= 0 or collateral_received_raw <= 0:
        return None

    amount = collateral_received_raw
    for leg in route.legs:
        quote = quote_leg(venue_registry, leg.pool, amount, leg.token_in, block)
        amount = int(quote.amount_out)
        if amount <= 0:
            return None

    debt = state.position(debt_asset)
    scale = Decimal(10) ** debt.decimals
    flash_fee_raw = _ceil_div(debt_to_cover_raw * int(flash_premium_bps), 10_000)
    gross_raw = amount - debt_to_cover_raw
    gross_usd = Decimal(gross_raw) / scale * debt.price_usd
    flash_fee_usd = Decimal(flash_fee_raw) / scale * debt.price_usd
    gas_usd = Decimal(gas_usd)
    reserve_usd = Decimal(reserve_usd)
    expected = gross_usd - flash_fee_usd - gas_usd - reserve_usd
    if expected <= 0:
        return None
    min_profit_raw = int((
        (gas_usd + reserve_usd) / debt.price_usd * scale
    ).to_integral_value(rounding=ROUND_CEILING)) + 1

    return LiquidationCandidate(
        block=block,
        borrower=state.borrower,
        debt_asset=debt_asset.lower(),
        collateral_asset=collateral_asset.lower(),
        debt_to_cover_raw=int(debt_to_cover_raw),
        collateral_received_raw=int(collateral_received_raw),
        unwind_out_raw=int(amount),
        flash_fee_raw=int(flash_fee_raw),
        min_profit_raw=min_profit_raw,
        gross_usd=gross_usd,
        flash_fee_usd=flash_fee_usd,
        gas_usd=gas_usd,
        reserve_usd=reserve_usd,
        expected_net_usd=expected,
        executable=True,
        route=route,
    )
