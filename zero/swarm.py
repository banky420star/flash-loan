"""Concurrency-safe primitives for ZERO's v0.5 market-scanning swarm."""

from __future__ import annotations

from dataclasses import dataclass
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
