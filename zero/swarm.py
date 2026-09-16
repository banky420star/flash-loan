"""Concurrency-safe primitives for ZERO's v0.5 market-scanning swarm."""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, fields
from decimal import Decimal
import threading
import time

from .calldata import build_uniswap_v3_steps
from .candidate import ArbitrageCandidate
from .keccak import keccak256, selector_hex
from .rpc import encode_address, encode_uint


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


@dataclass(frozen=True)
class WorkerSpec:
    worker_id: str
    manager_id: str
    primary_pair: tuple[str, str]
    role: str


@dataclass(frozen=True)
class ManagerSpec:
    manager_id: str
    worker_ids: tuple[str, ...]


@dataclass(frozen=True)
class TokenInfo:
    symbol: str
    address: str
    decimals: int
    price_usd: float


@dataclass(frozen=True)
class ScanContext:
    block: int
    aave_pool: str
    oracle: str
    premium_bps: int
    eth_price_usd: float
    gas_usd: float
    tokens: dict[str, TokenInfo]
    pool_states: dict[str, dict]


def build_topology(config: dict) -> tuple[list[ManagerSpec], list[WorkerSpec]]:
    swarm = config.get("swarm", {})
    manager_rows = swarm.get("managers", [])
    managers: list[ManagerSpec] = []
    workers: list[WorkerSpec] = []
    manager_ids: set[str] = set()
    worker_ids: set[str] = set()

    for row in manager_rows:
        manager_id = str(row.get("id", ""))
        if not manager_id or manager_id in manager_ids:
            raise ValueError("manager IDs must be unique and non-empty")
        manager_ids.add(manager_id)
        owned: list[str] = []
        for worker in row.get("workers", []):
            worker_id = str(worker.get("id", ""))
            pair = worker.get("pair", [])
            if not worker_id or worker_id in worker_ids:
                raise ValueError("worker IDs must be unique and non-empty")
            if not isinstance(pair, list) or len(pair) != 2 or not all(pair):
                raise ValueError(f"worker {worker_id} must define exactly one token pair")
            worker_ids.add(worker_id)
            owned.append(worker_id)
            workers.append(WorkerSpec(
                worker_id=worker_id,
                manager_id=manager_id,
                primary_pair=(str(pair[0]), str(pair[1])),
                role=str(worker.get("role", "scanner")),
            ))
        managers.append(ManagerSpec(manager_id=manager_id,
                                    worker_ids=tuple(owned)))

    if len(managers) != int(swarm.get("manager_count", 0)):
        raise ValueError("declared manager_count does not match manager definitions")
    if len(workers) != int(swarm.get("worker_count", 0)):
        raise ValueError("declared worker_count does not match worker definitions")
    return managers, workers


def build_token_registry(aave, block: int, *, pool: str | None = None,
                         oracle: str | None = None) -> dict[str, TokenInfo]:
    """Resolve Aave reserve token metadata once at one pinned block."""
    pool = pool or aave.pool_address(block=block)
    oracle = oracle or aave.oracle_address(block=block)
    registry: dict[str, TokenInfo] = {}
    for token in aave.reserves_list(pool, block=block):
        try:
            symbol = aave.symbol(token, block=block)
            decimals = int(aave.decimals(token, block=block))
            price_usd = float(aave.asset_price(oracle, token, block=block))
        except Exception:
            continue
        registry[symbol] = TokenInfo(
            symbol=symbol,
            address=token.lower(),
            decimals=decimals,
            price_usd=price_usd,
        )
    return registry


def _pool_config(token_a: TokenInfo, token_b: TokenInfo,
                 pool_address: str, fee_tier: int) -> dict:
    first, second = sorted(
        (token_a, token_b), key=lambda token: int(token.address, 16))
    return {
        "address": pool_address.lower(),
        "token0": first.address,
        "token1": second.address,
        "fee_tier": int(fee_tier),
        "fee_percent": int(fee_tier) / 10_000,
        "decimals0": first.decimals,
        "decimals1": second.decimals,
    }


def discover_uniswap_routes(engine, pair: tuple[str, str],
                            registry: dict[str, TokenInfo], block: int,
                            fee_tiers: list[int]) -> list[dict]:
    """Discover all ordered two-pool V3 cycles for one symbol pair at block."""
    base_symbol, quote_symbol = pair
    base = registry.get(base_symbol)
    quote = registry.get(quote_symbol)
    if base is None or quote is None:
        return []

    factory = engine.config["uniswap_v3_factory"]
    selector = selector_hex("getPool(address,address,uint24)")
    pools: list[dict] = []
    for fee in fee_tiers:
        data = (
            selector
            + encode_address(base.address)[2:]
            + encode_address(quote.address)[2:]
            + encode_uint(int(fee))[2:]
        )
        raw = engine.rpc.eth_call(factory, data, block=block)
        if not raw:
            continue
        pool_int = int.from_bytes(raw[:32], "big")
        if pool_int == 0:
            continue
        pool_address = "0x" + pool_int.to_bytes(20, "big").hex()
        pools.append(_pool_config(base, quote, pool_address, int(fee)))

    routes: list[dict] = []
    for i, pool_a in enumerate(pools):
        for j, pool_b in enumerate(pools):
            if i == j:
                continue
            key = RouteKey(
                chain_id=int(engine.config["chain_id"]),
                base=base.address,
                quote=quote.address,
                pool_a=pool_a["address"],
                pool_b=pool_b["address"],
                fee_a=pool_a["fee_tier"],
                fee_b=pool_b["fee_tier"],
                direction="base-to-quote-to-base",
            )
            routes.append({
                "block": int(block),
                "route_id": key.id,
                "name": (
                    f"{base_symbol}->{quote_symbol} {pool_a['fee_percent']}% | "
                    f"{quote_symbol}->{base_symbol} {pool_b['fee_percent']}%"
                ),
                "base_symbol": base_symbol,
                "quote_symbol": quote_symbol,
                "base": base.address,
                "quote": quote.address,
                "base_decimals": base.decimals,
                "quote_decimals": quote.decimals,
                "base_price_usd": base.price_usd,
                "quote_price_usd": quote.price_usd,
                "pools": [pool_a, pool_b],
            })
    return routes


def build_scan_context(engine, block: int,
                       route_catalog: list[dict]) -> ScanContext:
    """Freeze all shared Aave/oracle/pool state for one swarm scan block."""
    block = int(block)
    for route in route_catalog:
        if int(route.get("block", block)) != block:
            raise ValueError("mixed-block scan context")

    aave_pool = engine.aave.pool_address(block=block)
    oracle = engine.aave.oracle_address(block=block)
    premium_bps = engine.aave.flashloan_premium_total(aave_pool, block=block)
    tokens = build_token_registry(
        engine.aave, block, pool=aave_pool, oracle=oracle)
    eth_asset = engine.config["arbitrage"]["eth_for_gas"]
    eth_price_usd = float(engine.aave.asset_price(
        oracle, eth_asset, block=block))
    gas_usd = float(engine._gas_cost_usd(eth_price_usd))

    pool_states: dict[str, dict] = {}
    pool_configs: dict[str, dict] = {}
    for route in route_catalog:
        for pool_cfg in route.get("pools", []):
            address = str(pool_cfg.get("address", "")).lower()
            if not address or address in pool_configs:
                continue
            pool_configs[address] = pool_cfg

    for address, pool_cfg in pool_configs.items():
        pool = engine._pool(pool_cfg, block=block)
        pool_states[address] = pool.fetch_state(block=block)

    return ScanContext(
        block=block,
        aave_pool=aave_pool,
        oracle=oracle,
        premium_bps=int(premium_bps),
        eth_price_usd=eth_price_usd,
        gas_usd=gas_usd,
        tokens=tokens,
        pool_states=pool_states,
    )


class WorkAllocator:
    """Thread-safe primary route queues with cross-manager work stealing."""

    def __init__(self, workers: list[WorkerSpec], routes: list[dict], *,
                 block: int | None = None,
                 leases: RouteLeaseRegistry | None = None):
        self.workers = {worker.worker_id: worker for worker in workers}
        self._primary = {worker.worker_id: deque() for worker in workers}
        self._overflow = deque()
        self.block = block
        self.leases = leases
        self._lock = threading.Lock()

        pair_workers: dict[tuple[str, str], list[str]] = {}
        for worker in workers:
            pair_workers.setdefault(worker.primary_pair, []).append(worker.worker_id)
        pair_index: dict[tuple[str, str], int] = {}
        for route in routes:
            pair = (str(route.get("base_symbol", "")),
                    str(route.get("quote_symbol", "")))
            owners = pair_workers.get(pair, [])
            if not owners:
                self._overflow.append(route)
                continue
            index = pair_index.get(pair, 0)
            worker_id = owners[index % len(owners)]
            pair_index[pair] = index + 1
            self._primary[worker_id].append(route)

    def next_route(self, worker_id: str, *, allow_steal: bool = True) -> dict | None:
        with self._lock:
            own = self._primary.get(worker_id)
            if own is None:
                raise ValueError(f"unknown worker: {worker_id}")
            if own:
                return own.popleft()
            if not allow_steal:
                return None
            if self._overflow:
                return self._overflow.popleft()
            donors = sorted(
                ((len(queue), wid) for wid, queue in self._primary.items()
                 if wid != worker_id and queue),
                key=lambda item: (-item[0], item[1]),
            )
            if not donors:
                return None
            return self._primary[donors[0][1]].popleft()

    def claim(self, worker_id: str, route: dict) -> bool:
        if self.leases is None or self.block is None:
            return True
        return self.leases.claim(
            self.block, str(route["route_id"]), worker_id)


class SwarmSupervisor:
    """CEO: one pinned-block cycle across four managers and 20 workers."""

    def __init__(self, engine, config: dict, ledger, verifier=None, *,
                 worker_runner=None, catalog_builder=None):
        self.engine = engine
        self.config = config
        self.ledger = ledger
        self.verifier = verifier
        self.managers, self.workers = build_topology(config)
        self.worker_runner = worker_runner or self._run_worker
        self.catalog_builder = catalog_builder or self._build_catalog
        self.leases = RouteLeaseRegistry()

    def _build_catalog(self, block: int,
                       workers: list[WorkerSpec]) -> tuple[list[dict], ScanContext]:
        registry = build_token_registry(self.engine.aave, block)
        fee_tiers = [int(v) for v in self.config["swarm"].get(
            "fee_tiers", [100, 500, 3000, 10000])]
        routes: list[dict] = []
        seen_pairs: set[tuple[str, str]] = set()
        for worker in workers:
            if worker.primary_pair in seen_pairs:
                continue
            seen_pairs.add(worker.primary_pair)
            routes.extend(discover_uniswap_routes(
                self.engine, worker.primary_pair, registry, block, fee_tiers))
        context = build_scan_context(self.engine, block, routes)
        return routes, context

    def _run_worker(self, worker: WorkerSpec, allocator: WorkAllocator,
                    context: ScanContext) -> dict:
        rows: list[dict] = []
        scanned = 0
        duplicates = 0
        route_errors: list[dict] = []
        allow_steal = bool(self.config["swarm"].get("work_stealing", True))
        reserve = float(self.config["swarm"].get("model_reserve_usd", 0.0))
        while True:
            route = allocator.next_route(worker.worker_id, allow_steal=allow_steal)
            if route is None:
                break
            if not allocator.claim(worker.worker_id, route):
                duplicates += 1
                continue
            scanned += 1
            try:
                if route.get("route_kind") == "multidex_exact":
                    row = self.engine.scan_multidex_route(
                        context.block,
                        route,
                        model_reserve_usd=reserve,
                        context=context,
                    )
                elif route.get("route_kind") == "multihop_exact":
                    row = self.engine.scan_multihop_route(
                        context.block,
                        route,
                        model_reserve_usd=reserve,
                        context=context,
                    )
                else:
                    row = self.engine.scan_cycle_config(
                        context.block,
                        route,
                        swarm_mode=True,
                        model_reserve_usd=reserve,
                        context=context,
                    )
                if row is None:
                    continue
                row = dict(row)
                row["worker_id"] = worker.worker_id
                row["manager_id"] = worker.manager_id
                row["route_id"] = route["route_id"]
                row["route"] = route
                rows.append(row)
            except Exception as exc:
                route_errors.append({
                    "worker_id": worker.worker_id,
                    "manager_id": worker.manager_id,
                    "route_id": route.get("route_id"),
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return {
            "rows": rows,
            "routes_scanned": scanned,
            "duplicates_suppressed": duplicates,
            "route_errors": route_errors,
        }

    @staticmethod
    def _dedupe_routes(routes: list[dict]) -> tuple[list[dict], int]:
        seen: set[str] = set()
        unique: list[dict] = []
        duplicates = 0
        for route in routes:
            route_id = str(route.get("route_id", ""))
            if route_id and route_id in seen:
                duplicates += 1
                continue
            if route_id:
                seen.add(route_id)
            unique.append(route)
        return unique, duplicates

    @staticmethod
    def _candidate_from_row(row: dict) -> SwarmCandidate | None:
        expected = float(row.get("expected_net_usd", 0.0))
        candidate = row.get("candidate")
        route_id = str(row.get("route_id", ""))
        if expected <= 0 or not isinstance(candidate, dict) or not route_id:
            return None
        block = int(row["block"])
        size = float(row.get("size", candidate.get("loan_size", 0.0)))
        notional = float(row.get("loan_notional_usd", size))
        digest = keccak256(
            f"{block}|{route_id}|{size:.18g}".encode()).hex()
        return SwarmCandidate(
            candidate_id="0x" + digest,
            route_id=route_id,
            worker_id=str(row.get("worker_id", "")),
            manager_id=str(row.get("manager_id", "")),
            block=block,
            loan_size=size,
            gross_profit=float(row.get("gross_usd", 0.0)),
            flash_fee=float(row.get("flash_fee_usd", 0.0)),
            gas_cost=float(row.get("gas_usd", 0.0)),
            model_reserve=float(row.get("model_reserve_usd", 0.0)),
            expected_net=expected,
            roi=(expected / notional) if notional > 0 else 0.0,
            timestamp=time.time(),
            payload={"candidate": candidate, "route": row.get("route")},
        )

    def _persist_rows(self, rows: list[dict], errors: list[dict]) -> None:
        for row in sorted(rows, key=lambda item: str(item.get("route_id", ""))):
            candidate = row.get("candidate") or {}
            self.ledger.record(
                block=int(row.get("block", 0)),
                strategy="swarm_arbitrage",
                decision=str(row.get("decision", "REJECT")),
                asset=candidate.get("base_asset"),
                loan_size=row.get("size"),
                gross=row.get("gross_usd", row.get("gross")),
                net=row.get("expected_net_usd", row.get("net")),
                min_profit=candidate.get("min_profit"),
                reason=row.get("reason"),
                detail=row,
            )
        for error in sorted(errors, key=lambda item: (
                str(item.get("worker_id", "")), str(item.get("route_id", "")))):
            self.ledger.record(
                block=int(error.get("block", 0)),
                strategy="swarm_worker",
                decision="ERROR",
                reason=str(error.get("error", "worker error")),
                detail=error,
            )

    def _fork_payload(self, candidate: SwarmCandidate) -> dict:
        payload = candidate.payload or {}
        raw_candidate = payload.get("candidate")
        if not isinstance(raw_candidate, dict):
            raise ValueError("swarm candidate payload is missing executable candidate")
        names = {field.name for field in fields(ArbitrageCandidate)}
        normalized = ArbitrageCandidate(**{
            name: raw_candidate[name] for name in names
        })
        execution = self.config["arbitrage"]["execution"]
        steps = build_uniswap_v3_steps(
            normalized,
            execution["swap_router_02"],
            slippage_bps=int(execution.get("slippage_bps", 20)),
        )
        return {
            "candidate": normalized.as_dict(),
            "steps": [step.as_dict() for step in steps],
        }

    def _verify_candidates(self, candidates: list[SwarmCandidate]) -> tuple[int, int, int]:
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

        def verify_one(candidate: SwarmCandidate) -> bool:
            try:
                return int(self.verifier(self._fork_payload(candidate))) == 0
            except Exception:
                return False

        passed = 0
        failed = 0
        with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="zero-fork") as executor:
            futures = [executor.submit(verify_one, candidate)
                       for candidate in candidates]
            for future in as_completed(futures):
                if future.result():
                    passed += 1
                else:
                    failed += 1
        return len(candidates), passed, failed

    def run_block(self, block: int | None = None) -> dict:
        started = time.time()
        scan_block = int(block if block is not None else self.engine.rpc.block_number())
        routes, context = self.catalog_builder(scan_block, self.workers)
        unique_routes, duplicates = self._dedupe_routes(list(routes))
        allocator = WorkAllocator(
            self.workers, unique_routes, block=scan_block, leases=self.leases)
        max_workers = max(1, min(
            len(self.workers), int(self.config["swarm"].get(
                "max_rpc_concurrency", len(self.workers)))))

        rows: list[dict] = []
        errors: list[dict] = []
        routes_scanned = 0
        worker_failures = 0
        with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="zero-swarm") as executor:
            futures = {
                executor.submit(self.worker_runner, worker, allocator, context): worker
                for worker in self.workers
            }
            for future in as_completed(futures):
                worker = futures[future]
                try:
                    outcome = future.result()
                except Exception as exc:
                    worker_failures += 1
                    errors.append({
                        "block": scan_block,
                        "worker_id": worker.worker_id,
                        "manager_id": worker.manager_id,
                        "route_id": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    continue
                if isinstance(outcome, list):
                    rows.extend(outcome)
                    routes_scanned += len(outcome)
                    continue
                if not isinstance(outcome, dict):
                    continue
                worker_rows = outcome.get("rows", [])
                rows.extend(worker_rows)
                routes_scanned += int(outcome.get(
                    "routes_scanned", len(worker_rows)))
                duplicates += int(outcome.get("duplicates_suppressed", 0))
                route_errors = list(outcome.get("route_errors", []))
                for error in route_errors:
                    error.setdefault("block", scan_block)
                errors.extend(route_errors)
                worker_failures += len(route_errors)

        self._persist_rows(rows, errors)

        book = OpportunityBook()
        for row in rows:
            candidate = self._candidate_from_row(row)
            if candidate is None:
                continue
            if not book.add(candidate):
                duplicates += 1
        ranked = book.ranked()
        attempted, verified, verification_failed = self._verify_candidates(ranked)
        self.leases.expire_before(scan_block + 1)
        return {
            "block": scan_block,
            "active_workers": len(self.workers),
            "routes_scanned": routes_scanned,
            "worker_failures": worker_failures,
            "detected": len(rows),
            "positive_net": len(ranked),
            "duplicates_suppressed": duplicates,
            "best_expected_net": ranked[0].expected_net if ranked else None,
            "fork_verifications_attempted": attempted,
            "fork_verifications_passed": verified,
            "fork_verifications_failed": verification_failed,
            "elapsed_s": time.time() - started,
            "candidates": [candidate.as_dict() for candidate in ranked],
        }
