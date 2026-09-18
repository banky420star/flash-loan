from __future__ import annotations

from dataclasses import dataclass

from ..keccak import keccak256


@dataclass(frozen=True)
class PoolRef:
    venue_id: str
    address: str
    token0: str
    token1: str
    fee_tier: int | None = None
    pool_kind: str = "concentrated"

    def __post_init__(self) -> None:
        for value in (self.address, self.token0, self.token1):
            if not isinstance(value, str) or not value.startswith("0x") or len(value) != 42:
                raise ValueError("pool/token addresses must be 20-byte hex addresses")
        if not self.venue_id:
            raise ValueError("venue_id is required")

    @property
    def canonical(self) -> str:
        return "|".join([
            self.venue_id,
            self.address.lower(),
            self.token0.lower(),
            self.token1.lower(),
            str(self.fee_tier if self.fee_tier is not None else "dynamic"),
            self.pool_kind,
        ])

    @property
    def id(self) -> str:
        return "0x" + keccak256(self.canonical.encode()).hex()

@dataclass(frozen=True)
class VenueQuote:
    amount_out: int
    gas_estimate: int = 0
    fee_used: int | None = None


@dataclass
class VenueAdapter:
    venue_id: str
    rpc: object
    factory: str
    router: str
    quoter: str | None
    fee_tiers: tuple[int, ...] = ()
    exact_quote_supported: bool = False
    execution_supported: bool = False
    family: str = "unknown"

    def discover_pair(self, token_a: str, token_b: str,
                      block: int) -> list[PoolRef]:
        raise NotImplementedError

    def discover_pairs(self, pairs: list[tuple[str, str]], block: int, *,
                       max_batch: int = 100) -> dict[tuple[str, str], list[PoolRef]]:
        return {pair: self.discover_pair(pair[0], pair[1], int(block))
                for pair in pairs}

    def quote_exact_input(self, pool: PoolRef, token_in: str,
                          amount_in: int, block: int) -> VenueQuote:
        raise NotImplementedError
