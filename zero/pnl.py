"""Structured fork-P&L integration for the ZERO swarm supervisor.

This module extends the proven v0.5 scheduler without changing its scanning or
leasing logic. Fork workers return immutable `ForkResult` values; SQLite writes
happen only after the worker futures rejoin the CEO/supervisor thread.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .fork import ForkResult
from .liquidation_calldata import build_liquidation_steps
from .liquidation_swarm import scan_liquidation_watchlist
from .reserve import AdaptiveReserve, ReserveEstimate
from .routes import build_multihop_route_configs
from .swarm import SwarmCandidate, SwarmSupervisor
from .swarm_batch import (
    build_scan_context_batched,
    build_token_registry_batched,
    discover_uniswap_routes_batched,
)
from .venues.base import PoolRef
from .venues.multidex import discover_route_configs
from .venues.registry import build_venue_registry


class PnlSwarmSupervisor(SwarmSupervisor):
    """Swarm supervisor with learned reserve, batched reads, and fork P&L."""

    def _extra_candidates(self, block: int, context) -> list[SwarmCandidate]:
        liq_cfg = self.config.get("liquidation", {}) or {}
        if not (liq_cfg.get("borrowers") or liq_cfg.get("watchlist")):
            return []
        venues = getattr(self.engine, "venues", None)
        if not isinstance(venues, dict):
            return []
        candidates, errors = scan_liquidation_watchlist(
            self.engine, self.config, context, venues,
            model_reserve_usd=float(getattr(
                self, "_cycle_model_reserve_usd",
                self.config.get("swarm", {}).get("model_reserve_usd", 0.0))))
        for candidate in candidates:
            raw = ((candidate.payload or {}).get("candidate") or {})
            executable = bool(raw.get("executable", False))
            self.ledger.record(
                block=int(candidate.block),
                strategy="swarm_liquidation",
                decision="PASS" if executable else "OBSERVE",
                asset=raw.get("base_asset"),
                loan_size=float(candidate.loan_size),
                gross=float(candidate.gross_profit),
                net=float(candidate.expected_net),
                min_profit=raw.get("min_profit"),
                reason="ok" if executable else "fork_encoder_pending",
                detail=candidate.as_dict(),
            )
        for error in errors:
            self.ledger.record(
                block=int(error.get("block", block)),
                strategy="swarm_liquidation_worker",
                decision="ERROR",
                reason=str(error.get("error", "liquidation scan error")),
                detail=error,
            )
        return candidates

    def _reserve_estimate(self) -> ReserveEstimate:
        swarm_cfg = self.config.get("swarm", {})
        adaptive = swarm_cfg.get("adaptive_reserve", {}) or {}
        static = float(swarm_cfg.get("model_reserve_usd", 0.0) or 0.0)
        if not bool(adaptive.get("enabled", False)):
            return ReserveEstimate(static, 0)

        policy = AdaptiveReserve(
            lookback=int(adaptive.get("lookback", 100)),
            min_samples=int(adaptive.get("min_samples", 5)),
            quantile=float(adaptive.get("quantile", 0.90)),
            floor_usd=float(adaptive.get("floor_usd", 0.0)),
            cap_usd=float(adaptive.get("cap_usd", 25.0)),
            bootstrap_reserve_usd=float(
                adaptive.get("bootstrap_reserve_usd", static)),
        )
        rows = self.ledger.fork_economics(limit=policy.lookback)
        return policy.estimate(rows)

    def _build_catalog(self, block: int, workers) -> tuple[list[dict], object]:
        """Build the same route catalog/context using bounded pinned batches."""
        started = time.perf_counter()
        block = int(block)
        swarm_cfg = self.config.get("swarm", {})
        max_batch = int(swarm_cfg.get("max_rpc_batch", 100))
        if max_batch <= 0:
            raise ValueError("swarm.max_rpc_batch must be positive")

        aave_pool = self.engine.aave.pool_address(block=block)
        oracle = self.engine.aave.oracle_address(block=block)
        registry = build_token_registry_batched(
            self.engine, block, pool=aave_pool, oracle=oracle,
            max_batch=max_batch)
        fee_tiers = [int(value) for value in swarm_cfg.get(
            "fee_tiers", [100, 500, 3000, 10000])]
        venue_registry = build_venue_registry(self.config, self.engine.rpc)

        routes: list[dict] = []
        multidex_routes: list[dict] = []
        seen_pairs: set[tuple[str, str]] = set()
        for worker in workers:
            pair = worker.primary_pair
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            routes.extend(discover_uniswap_routes_batched(
                self.engine, pair, registry, block, fee_tiers,
                max_batch=max_batch))
            discovered = discover_route_configs(
                venue_registry, pair, registry,
                int(self.config.get("chain_id", 42161)), block)
            routes.extend(discovered)
            multidex_routes.extend(discovered)

        if bool(swarm_cfg.get("multihop_enabled", True)):
            pool_by_id: dict[str, PoolRef] = {}
            for row in multidex_routes:
                for key in ("leg1", "leg2"):
                    raw = row.get(key)
                    if not isinstance(raw, dict):
                        continue
                    pool = PoolRef(**raw)
                    pool_by_id[pool.id] = pool
            pools = [pool_by_id[key] for key in sorted(pool_by_id)]
            max_routes = int(swarm_cfg.get("multihop_max_routes_per_pair", 8))
            if max_routes > 0 and pools:
                for pair in sorted(seen_pairs):
                    routes.extend(build_multihop_route_configs(
                        int(self.config.get("chain_id", 42161)), pair, registry,
                        pools, block=block, max_routes=max_routes))

        context = build_scan_context_batched(
            self.engine, block, routes, tokens=registry,
            aave_pool=aave_pool, oracle=oracle, max_batch=max_batch)
        self._last_catalog_ms = (time.perf_counter() - started) * 1000.0
        return routes, context

    def run_block(self, block: int | None = None) -> dict:
        """Resolve one reserve on the CEO thread and freeze it for this cycle."""
        estimate = self._reserve_estimate()
        swarm_cfg = self.config.setdefault("swarm", {})
        previous = swarm_cfg.get("model_reserve_usd", 0.0)
        self._cycle_model_reserve_usd = float(estimate.value_usd)
        self._last_catalog_ms = 0.0
        self._last_verify_ms = 0.0
        swarm_cfg["model_reserve_usd"] = self._cycle_model_reserve_usd
        try:
            result = super().run_block(block)
        finally:
            swarm_cfg["model_reserve_usd"] = previous

        elapsed_ms = max(0.0, float(result.get("elapsed_s", 0.0)) * 1000.0)
        catalog_ms = max(0.0, float(self._last_catalog_ms))
        verify_ms = max(0.0, float(self._last_verify_ms))
        scan_ms = max(0.0, elapsed_ms - catalog_ms - verify_ms)
        result["adaptive_reserve_usd"] = self._cycle_model_reserve_usd
        result["reserve_samples"] = int(estimate.samples)
        result["catalog_ms"] = catalog_ms
        result["scan_ms"] = scan_ms
        result["verify_ms"] = verify_ms
        return result

    def _fork_payload(self, candidate: SwarmCandidate) -> dict:
        source = candidate.payload or {}
        raw_candidate = source.get("candidate") or {}
        route = source.get("route") or {}
        if raw_candidate.get("route_kind") == "liquidation_exact":
            execution = self.config["arbitrage"]["execution"]
            aave_pool = self.engine.aave.pool_address(block=candidate.block)
            steps = build_liquidation_steps(
                raw_candidate, aave_pool, execution["swap_router_02"],
                slippage_bps=int(execution.get("slippage_bps", 20)))
            payload = {
                "kind": "liquidation",
                "candidate": dict(raw_candidate),
                "steps": [step.as_dict() for step in steps],
            }
            strategy = "swarm_liquidation"
            base_price_usd = float(raw_candidate.get("base_price_usd", 1.0))
        else:
            payload = super()._fork_payload(candidate)
            strategy = "swarm_arbitrage"
            base_price_usd = float(route.get("base_price_usd", 1.0))
        payload["verification"] = {
            "candidate_id": candidate.candidate_id,
            "route_id": candidate.route_id,
            "strategy": strategy,
            "base_price_usd": base_price_usd,
            "model_reserve_usd": float(candidate.model_reserve),
            "gas_limit": int(self.config.get("gas_limit", 0) or 0),
        }
        return payload

    def _verify_candidates(self, candidates: list[SwarmCandidate]) -> tuple[int, int, int]:
        started = time.perf_counter()
        try:
            candidates = [
                candidate for candidate in candidates
                if bool(((candidate.payload or {}).get("candidate") or {}).get(
                    "executable", True))
            ]
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

            # Persist after all fork futures rejoin this caller. This keeps
            # SQLite writes serialized on the supervisor thread.
            for result in sorted(
                    structured,
                    key=lambda item: (item.block, item.strategy, item.gas_used)):
                self.ledger.record_fork_verification(result)

            return len(candidates), passed, failed
        finally:
            self._last_verify_ms = (time.perf_counter() - started) * 1000.0
