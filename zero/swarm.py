"""Concurrency-safe primitives for ZERO's v0.5 market-scanning swarm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import threading

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
