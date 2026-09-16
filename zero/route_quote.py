from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .routes import RouteCandidate
from .venues.base import VenueQuote
from .venues.quotes import quote_leg


class RouteQuoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class RouteQuote:
    route_id: str
    block: int
    amount_in_raw: int
    amount_out_raw: int
    gas_estimate: int
    leg_quotes: tuple[VenueQuote, ...]


@dataclass(frozen=True)
class RouteEconomics:
    gross_raw: int
    flash_fee_raw: int
    gross_usd: Decimal
    flash_fee_usd: Decimal
    gas_usd: Decimal
    reserve_usd: Decimal
    expected_net_usd: Decimal

    @property
    def positive(self) -> bool:
        return self.expected_net_usd > Decimal("0")


def quote_route(route: RouteCandidate, amount_in_raw: int, block: int,
                venue_registry: dict[str, object]) -> RouteQuote:
    amount_in_raw = int(amount_in_raw)
    block = int(block)
    if amount_in_raw <= 0:
        raise RouteQuoteError("route input must be positive")
    amount = amount_in_raw
    quotes: list[VenueQuote] = []
    total_gas = 0
    for leg in route.legs:
        try:
            quote = quote_leg(
                venue_registry, leg.pool, amount, leg.token_in, block)
        except Exception as exc:
            raise RouteQuoteError(
                f"{leg.pool.venue_id} quote failed: {type(exc).__name__}: {exc}") from exc
        if int(quote.amount_out) <= 0:
            raise RouteQuoteError("route leg returned non-positive output")
        quotes.append(quote)
        amount = int(quote.amount_out)
        total_gas += int(quote.gas_estimate)
    return RouteQuote(
        route_id=route.id,
        block=block,
        amount_in_raw=amount_in_raw,
        amount_out_raw=amount,
        gas_estimate=total_gas,
        leg_quotes=tuple(quotes),
    )


def evaluate_route_economics(quote: RouteQuote, *, base_decimals: int,
                             base_price_usd: Decimal,
                             premium_bps: int,
                             gas_usd: Decimal,
                             reserve_usd: Decimal) -> RouteEconomics:
    base_decimals = int(base_decimals)
    premium_bps = int(premium_bps)
    if base_decimals < 0:
        raise ValueError("base_decimals must be non-negative")
    if premium_bps < 0:
        raise ValueError("premium_bps must be non-negative")
    scale = Decimal(10) ** base_decimals
    gross_raw = int(quote.amount_out_raw) - int(quote.amount_in_raw)
    flash_fee_raw = (
        int(quote.amount_in_raw) * premium_bps + 5_000) // 10_000
    base_price_usd = Decimal(base_price_usd)
    gas_usd = Decimal(gas_usd)
    reserve_usd = Decimal(reserve_usd)
    gross_usd = (Decimal(gross_raw) / scale) * base_price_usd
    flash_fee_usd = (Decimal(flash_fee_raw) / scale) * base_price_usd
    expected = gross_usd - flash_fee_usd - gas_usd - reserve_usd
    return RouteEconomics(
        gross_raw=gross_raw,
        flash_fee_raw=flash_fee_raw,
        gross_usd=gross_usd,
        flash_fee_usd=flash_fee_usd,
        gas_usd=gas_usd,
        reserve_usd=reserve_usd,
        expected_net_usd=expected,
    )
