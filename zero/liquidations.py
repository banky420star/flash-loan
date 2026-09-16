from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

CLOSE_FACTOR_HF_THRESHOLD = Decimal('0.95')
DEFAULT_CLOSE_FACTOR_BPS = 5_000
MAX_CLOSE_FACTOR_BPS = 10_000
MIN_BASE_MAX_CLOSE_FACTOR_USD = Decimal('2000')


@dataclass(frozen=True)
class ReservePosition:
    asset: str
    decimals: int
    price_usd: Decimal
    collateral_raw: int
    debt_raw: int
    usage_as_collateral_enabled: bool
    liquidation_threshold_bps: int
    liquidation_bonus_bps: int
    liquidation_protocol_fee_bps: int

    @property
    def collateral_usd(self) -> Decimal:
        return (Decimal(self.collateral_raw) / (Decimal(10) ** self.decimals)) * self.price_usd

    @property
    def debt_usd(self) -> Decimal:
        return (Decimal(self.debt_raw) / (Decimal(10) ** self.decimals)) * self.price_usd


@dataclass(frozen=True)
class LiquidationState:
    block: int
    borrower: str
    pool: str
    data_provider: str
    health_factor: Decimal
    total_collateral_usd: Decimal
    total_debt_usd: Decimal
    positions: tuple[ReservePosition, ...]

    @property
    def liquidatable(self) -> bool:
        return self.health_factor < Decimal('1') and self.total_debt_usd > 0

    def position(self, asset: str) -> ReservePosition:
        target = asset.lower()
        for row in self.positions:
            if row.asset.lower() == target:
                return row
        raise KeyError(asset)

    def close_factor_bps(self, collateral_asset: str, debt_asset: str) -> int:
        collateral = self.position(collateral_asset)
        debt = self.position(debt_asset)
        if not self.liquidatable:
            return 0
        if (self.health_factor > CLOSE_FACTOR_HF_THRESHOLD
                and collateral.collateral_usd >= MIN_BASE_MAX_CLOSE_FACTOR_USD
                and debt.debt_usd >= MIN_BASE_MAX_CLOSE_FACTOR_USD):
            return DEFAULT_CLOSE_FACTOR_BPS
        return MAX_CLOSE_FACTOR_BPS

    def max_debt_to_cover_raw(self, collateral_asset: str, debt_asset: str) -> int:
        debt = self.position(debt_asset)
        factor = self.close_factor_bps(collateral_asset, debt_asset)
        if factor == 0:
            return 0
        if factor == MAX_CLOSE_FACTOR_BPS:
            return int(debt.debt_raw)
        max_usd = self.total_debt_usd * Decimal(factor) / Decimal(10_000)
        raw = (max_usd / debt.price_usd * (Decimal(10) ** debt.decimals)).to_integral_value(
            rounding=ROUND_FLOOR)
        return min(int(debt.debt_raw), int(raw))


def build_liquidation_state(aave, borrower: str, block: int) -> LiquidationState:
    block = int(block)
    pool = aave.pool_address(block=block)
    data_provider = aave.data_provider_address(block=block)
    oracle = aave.oracle_address(block=block)
    account = aave.account_data(pool, borrower, block=block)
    positions = []
    for asset in aave.reserves_list(pool, block=block):
        user = aave.user_reserve_data(data_provider, asset, borrower, block=block)
        collateral_raw = int(user.get('a_token_balance_raw', 0))
        debt_raw = int(user.get('debt_raw', 0))
        if collateral_raw <= 0 and debt_raw <= 0:
            continue
        cfg = aave.reserve_configuration(data_provider, asset, block=block)
        fee = aave.liquidation_protocol_fee(data_provider, asset, block=block)
        price = Decimal(str(aave.asset_price(oracle, asset, block=block)))
        positions.append(ReservePosition(
            asset=asset.lower(),
            decimals=int(cfg['decimals']),
            price_usd=price,
            collateral_raw=collateral_raw,
            debt_raw=debt_raw,
            usage_as_collateral_enabled=bool(user.get('usage_as_collateral_enabled', False)),
            liquidation_threshold_bps=int(cfg['liquidation_threshold_bps']),
            liquidation_bonus_bps=int(cfg['liquidation_bonus_bps']),
            liquidation_protocol_fee_bps=int(fee),
        ))
    return LiquidationState(
        block=block,
        borrower=borrower.lower(),
        pool=pool.lower(),
        data_provider=data_provider.lower(),
        health_factor=Decimal(str(account['health_factor'])),
        total_collateral_usd=Decimal(str(account['collateral_usd'])),
        total_debt_usd=Decimal(str(account['debt_usd'])),
        positions=tuple(positions),
    )
