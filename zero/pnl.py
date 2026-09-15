"""Structured fork-P&L integration for the ZERO swarm supervisor.

This module extends the proven v0.5 scheduler without changing its scanning or
leasing logic. Fork workers return immutable `ForkResult` values; SQLite writes
happen only after the worker futures rejoin the CEO/supervisor thread.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .fork import ForkResult
from .swarm import SwarmCandidate, SwarmSupervisor


class PnlSwarmSupervisor(SwarmSupervisor):
    """Swarm supervisor that enriches and persists structured fork outcomes."""

    def _fork_payload(self, candidate: SwarmCandidate) -> dict:
        payload = super()._fork_payload(candidate)
        source = candidate.payload or {}
        route = source.get("route") or {}
        payload["verification"] = {
            "candidate_id": candidate.candidate_id,
            "route_id": candidate.route_id,
            "strategy": "swarm_arbitrage",
            "base_price_usd": float(route.get("base_price_usd", 1.0)),
            "model_reserve_usd": float(candidate.model_reserve),
            "gas_limit": int(self.config.get("gas_limit", 0) or 0),
        }
        return payload

    def _verify_candidates(self, candidates: list[SwarmCandidate]) -> tuple[int, int, int]:
        if (self.verifier is None
                or not self.config["swarm"].get("verify_positive_candidates", True)
                or not candidates):
            return 0, 0, 0

        max_workers = max(1, min(
            len(candidates),
            int(self.config["swarm"].get("max_fork_concurrency", 1)),
        ))

        def verify_one(candidate: SwarmCandidate):
            try:
                outcome = self.verifier(self._fork_payload(candidate))
                if isinstance(outcome, ForkResult):
                    return outcome.success, outcome
                return int(outcome) == 0, None
            except Exception:
                return False, None

        passed = 0
        failed = 0
        structured: list[ForkResult] = []
        with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="zero-fork") as executor:
            futures = [executor.submit(verify_one, candidate)
                       for candidate in candidates]
            for future in as_completed(futures):
                success, result = future.result()
                if success:
                    passed += 1
                else:
                    failed += 1
                if result is not None:
                    structured.append(result)

        # Persist after all fork futures rejoin this caller. This keeps SQLite
        # writes serialized on the supervisor thread even when fork concurrency
        # is raised above one later.
        for result in sorted(
                structured,
                key=lambda item: (item.block, item.strategy, item.gas_used)):
            self.ledger.record_fork_verification(result)

        return len(candidates), passed, failed
