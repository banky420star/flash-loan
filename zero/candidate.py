"""Normalized arbitrage candidate values for deterministic fork execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


_BPS = 10_000


def _raw_floor(value: float, decimals: int) -> int:
    scaled = Decimal(str(value)) * (Decimal(10) ** decimals)
    return int(scaled.to_integral_value(rounding=ROUND_FLOOR))


def _raw_ceil(value: float, decimals: int) -> int:
    scaled = Decimal(str(value)) * (Decimal(10) ** decimals)
    return int(scaled.to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class ArbitrageCandidate:
    block: int
    name: str
    base_asset: str
    quote_asset: str
    base_decimals: int
    quote_decimals: int
    loan_size: float
    hop1_expected_out: float
    hop2_expected_out: float
    fee1: int
    fee2: int
    flash_premium_bps: int
    gas_cost_usd: float
    gross_profit: float
    predicted_net: float
    min_profit: float

    @property
    def loan_amount_raw(self) -> int:
        return _raw_floor(self.loan_size, self.base_decimals)

    @property
    def hop1_expected_out_raw(self) -> int:
        return _raw_floor(self.hop1_expected_out, self.quote_decimals)

    @property
    def hop2_expected_out_raw(self) -> int:
        return _raw_floor(self.hop2_expected_out, self.base_decimals)

    @property
    def flash_fee_raw(self) -> int:
        # Aave PercentageMath.percentMul rounds half-up by adding half the
        # percentage factor before integer division.
        return (self.loan_amount_raw * self.flash_premium_bps + _BPS // 2) // _BPS

    @property
    def flash_fee(self) -> float:
        return self.flash_fee_raw / (10 ** self.base_decimals)

    @property
    def min_profit_raw(self) -> int:
        # Minimum profit is a lower bound, so never round it down.
        return _raw_ceil(self.min_profit, self.base_decimals)

    def as_dict(self) -> dict:
        out = asdict(self)
        out.update({
            "loan_amount_raw": self.loan_amount_raw,
            "hop1_expected_out_raw": self.hop1_expected_out_raw,
            "hop2_expected_out_raw": self.hop2_expected_out_raw,
            "flash_fee_raw": self.flash_fee_raw,
            "flash_fee": self.flash_fee,
            "min_profit_raw": self.min_profit_raw,
        })
        return out
