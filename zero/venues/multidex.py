from __future__ import annotations

from dataclasses import dataclass

from ..keccak import keccak256
from .base import PoolRef


@dataclass(frozen=True)
class TwoLegRoute:
    chain_id: int
    block: int
    base: str
    quote: str
    leg1: PoolRef
    leg2: PoolRef

    @property
    def canonical(self) -> str:
        return "|".join([
            str(self.chain_id), self.base.lower(), self.quote.lower(),
            self.leg1.id, self.leg2.id, "base-to-quote-to-base",
        ])

    @property
    def id(self) -> str:
        return "0x" + keccak256(self.canonical.encode()).hex()


def build_two_leg_routes(chain_id: int, base: str, quote: str,
                         pools: list[PoolRef], *, block: int) -> list[TwoLegRoute]:
    routes = []
    for first in pools:
        for second in pools:
            if first.id == second.id:
                continue
            routes.append(TwoLegRoute(int(chain_id), int(block),
                                      base.lower(), quote.lower(), first, second))
    return routes
