"""Concurrency-safe primitives for ZERO's v0.5 market-scanning swarm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import threading

from .keccak import keccak256


@dataclass(frozen=True)
class RouteKey:
    chain_id: int
    base: str
    quote: str
    pool_a: str
    pool_b: str
    fee_a: int
    fee_b: int
    direction: str

    @property
    def canonical(self) -> str:
        return "|".join([
            str(self.chain_id),
            self.base.lower(),
            self.quote.lower(),
            self.pool_a.lower(),
            self.pool_b.lower(),
            str(self.fee_a),
            str(self.fee_b),
            self.direction,
        ])

    @property
    def id(self) -> str:
        return "0x" + keccak256(self.canonical.encode()).hex()


class RouteLeaseRegistry:
    """Own one `(block, route)` lease at a time across concurrent workers."""

    def __init__(self):
        self._leases: dict[tuple[int, str], str] = {}
        self._lock = threading.Lock()

    def claim(self, block: int, route_id: str, worker_id: str) -> bool:
        key = (int(block), route_id)
        with self._lock:
            if key in self._leases:
                return False
            self._leases[key] = worker_id
            return True

    def release(self, block: int, route_id: str, worker_id: str) -> None:
        key = (int(block), route_id)
        with self._lock:
            owner = self._leases.get(key)
            if owner != worker_id:
                raise ValueError("route lease can only be released by its owner")
            del self._leases[key]

    def expire_before(self, block: int) -> None:
        cutoff = int(block)
        with self._lock:
            stale = [key for key in self._leases if key[0] < cutoff]
            for key in stale:
                del self._leases[key]


@dataclass(frozen=True)
class SwarmCandidate:
    candidate_id: str
    route_id: str
    worker_id: str
    manager_id: str
    block: int
    loan_size: float
    gross_profit: float
    flash_fee: float
    gas_cost: float
    model_reserve: float
    expected_net: float
    roi: float
    timestamp: float
    payload: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def swarm_expected_net(gross: float, flash_fee: float, gas: float,
                       model_reserve: float) -> float:
    value = (
        Decimal(str(gross))
        - Decimal(str(flash_fee))
        - Decimal(str(gas))
        - Decimal(str(model_reserve))
    )
    return float(value)


class OpportunityBook:
    """One positive-net candidate per route per block, ranked by expected net."""

    def __init__(self):
        self._items: dict[tuple[int, str], SwarmCandidate] = {}

    def add(self, candidate: SwarmCandidate) -> bool:
        if candidate.expected_net <= 0:
            return False
        key = (int(candidate.block), candidate.route_id)
        if key in self._items:
            return False
        self._items[key] = candidate
        return True

    def ranked(self) -> list[SwarmCandidate]:
        return sorted(
            self._items.values(),
            key=lambda candidate: candidate.expected_net,
            reverse=True,
        )
