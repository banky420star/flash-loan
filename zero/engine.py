"""Shadow-mode engine: live chain data in, decisions recorded, nothing sent.

HARD INVARIANT: this module has no signer, no private key, and no code path
that broadcasts a transaction. Its output is the SQLite ledger and stdout.
"""

from decimal import Decimal, ROUND_FLOOR
import math
import time

from .aave import AaveV3
from .candidate import ArbitrageCandidate
from .rpc import Rpc, RpcError
from .route_quote import RouteQuoteError, evaluate_route_economics, quote_route
from .routes import RouteCandidate, RouteLeg
from .strategies.arbitrage import Cycle, best_opportunity, sweep_sizes
from .strategies.liquidation import gate_liquidations, scan_watchlist
from .swarm import ScanContext, swarm_expected_net
from .uniswap_quoter import QUOTER_V2_ARBITRUM, UniswapV3Quoter
from .venues.base import PoolRef
from .venues.multidex import TwoLegRoute
from .venues.quotes import quote_leg, route_execution_ready
from .venues.registry import build_venue_registry


class ShadowEngine:
    def __init__(self, rpc_url: str, config: dict, ledger):
        self.rpc = Rpc(rpc_url)
        self.config = config
        self.ledger = ledger
        self.aave = AaveV3(self.rpc, config["aave_provider"])
        self.quoter = UniswapV3Quoter(
            self.rpc, config.get("uniswap_v3_quoter_v2", QUOTER_V2_ARBITRUM))
        self.venues = build_venue_registry(config, self.rpc)
        self.gas_limit = config.get("gas_limit", 2_000_000)
        self.gas_price_gwei = config.get("gas_price_gwei", 0.1)

    def _gas_cost_usd(self, eth_price: float) -> float:
        eth = self.gas_limit * self.gas_price_gwei / 1e9
        return eth * eth_price

    @staticmethod
    def flash_economics(*, gross: float, loan_size: float,
                        premium_bps: int, gas_usd: float) -> dict:
        flash_fee = loan_size * premium_bps / 10_000
        return {"flash_fee": flash_fee,
                "net": gross - gas_usd - flash_fee}

    @staticmethod
    def normalize_candidate(*, block: int, cycle_config: dict, best: dict,
                            premium_bps: int, gas_usd: float,
                            min_profit: float,
                            predicted_net: float | None = None) -> ArbitrageCandidate:
        base = cycle_config["base"]
        pools = cycle_config["pools"]
        if len(pools) != 2:
            raise ValueError("v0.4 candidates require exactly two pools")
        pair = pools[0]
        if pair["token0"].lower() == base.lower():
            quote = pair["token1"]
        elif pair["token1"].lower() == base.lower():
            quote = pair["token0"]
        else:
            raise ValueError("base asset is not present in first pool")
        if predicted_net is None:
            economics = ShadowEngine.flash_economics(
                gross=best["gross"], loan_size=best["size"],
                premium_bps=premium_bps, gas_usd=gas_usd)
            predicted_net = economics["net"]
        return ArbitrageCandidate(
            block=block,
            name=cycle_config.get("name", "unnamed-cycle"),
            base_asset=base,
            quote_asset=quote,
            base_decimals=cycle_config["base_decimals"],
            quote_decimals=cycle_config["quote_decimals"],
            loan_size=best["size"],
            hop1_expected_out=best["hop1_out"],
            hop2_expected_out=best["hop2_out"],
            fee1=pools[0]["fee_tier"],
            fee2=pools[1]["fee_tier"],
            flash_premium_bps=premium_bps,
            gas_cost_usd=gas_usd,
            gross_profit=best["gross"],
            predicted_net=predicted_net,
            min_profit=min_profit,
        )

    def _exact_swarm_preflight(self, *, block: int, cycle_config: dict,
                               best: dict, base_price_usd: float,
                               premium_bps: int, gas_usd: float,
                               model_reserve_usd: float) -> dict:
        """Re-quote one locally positive route through QuoterV2 at `block`."""
        base_decimals = int(cycle_config["base_decimals"])
        quote_decimals = int(cycle_config["quote_decimals"])
        base_scale = Decimal(10) ** base_decimals
        loan_raw = int(
            (Decimal(str(best["size"])) * base_scale).to_integral_value(
                rounding=ROUND_FLOOR))
        if loan_raw <= 0:
            raise ValueError("exact preflight loan amount must be positive")

        pools = cycle_config["pools"]
        base = cycle_config["base"]
        quote = cycle_config["quote"]
        hop1 = self.quoter.quote_exact_input_single(
            token_in=base,
            token_out=quote,
            fee=int(pools[0]["fee_tier"]),
            amount_in=loan_raw,
            block=block,
        )
        hop2 = self.quoter.quote_exact_input_single(
            token_in=quote,
            token_out=base,
            fee=int(pools[1]["fee_tier"]),
            amount_in=int(hop1.amount_out),
            block=block,
        )

        loan_base = loan_raw / (10 ** base_decimals)
        hop1_out = int(hop1.amount_out) / (10 ** quote_decimals)
        hop2_out = int(hop2.amount_out) / (10 ** base_decimals)
        gross_base = hop2_out - loan_base
        gross_usd = gross_base * base_price_usd
        flash_fee_raw = (loan_raw * premium_bps + 5_000) // 10_000
        flash_fee_base = flash_fee_raw / (10 ** base_decimals)
        flash_fee_usd = flash_fee_base * base_price_usd
        expected_net_usd = swarm_expected_net(
            gross_usd, flash_fee_usd, gas_usd, model_reserve_usd)

        out = dict(best)
        out.update({
            "size": loan_base,
            "hop1_out": hop1_out,
            "hop2_out": hop2_out,
            "gross": gross_base,
            "gross_usd": gross_usd,
            "flash_fee_base": flash_fee_base,
            "flash_fee_usd": flash_fee_usd,
            "expected_net_usd": expected_net_usd,
            "initialized_ticks_crossed": (
                int(hop1.initialized_ticks_crossed)
                + int(hop2.initialized_ticks_crossed)
            ),
            "quoter_gas_estimate": int(hop1.gas_estimate) + int(hop2.gas_estimate),
        })
        return out

    def scan_multihop_route(self, block: int, route_config: dict, *,
                            model_reserve_usd: float = 0.0,
                            context: ScanContext | None = None) -> dict | None:
        """Exact-quote a bounded cyclic route at one pinned block."""
        block = int(block)
        if int(route_config.get("block", block)) != block:
            raise ValueError("mixed-block multi-hop route")
        if context is None or int(context.block) != block:
            raise ValueError("multi-hop scan requires a pinned ScanContext")

        legs = tuple(
            RouteLeg(
                PoolRef(**row["pool"]),
                str(row["token_in"]),
                str(row["token_out"]),
            )
            for row in route_config.get("legs", [])
        )
        route = RouteCandidate(
            int(self.config.get("chain_id", 42161)),
            str(route_config["base"]),
            legs,
        )
        base_decimals = int(route_config["base_decimals"])
        base_price = float(route_config.get("base_price_usd", 0.0) or 0.0)
        symbol = route_config.get("base_symbol")
        if symbol in context.tokens:
            base_price = float(context.tokens[symbol].price_usd)
        if base_price <= 0:
            raise ValueError("multi-hop base price must be positive")

        def evaluate(usd_size: float):
            loan_raw = int((Decimal(str(usd_size)) / Decimal(str(base_price))
                            * (Decimal(10) ** base_decimals)).to_integral_value(
                                rounding=ROUND_FLOOR))
            if loan_raw <= 0:
                return None
            try:
                quoted = quote_route(route, loan_raw, block, self.venues)
            except RouteQuoteError as exc:
                cause = exc.__cause__
                if isinstance(cause, RpcError) and not str(cause).startswith("RPC error:"):
                    raise cause
                return None
            economics = evaluate_route_economics(
                quoted,
                base_decimals=base_decimals,
                base_price_usd=Decimal(str(base_price)),
                premium_bps=int(context.premium_bps),
                gas_usd=Decimal(str(context.gas_usd)),
                reserve_usd=Decimal(str(model_reserve_usd)),
            )
            return quoted, economics, loan_raw

        swarm_cfg = self.config.get("swarm", {})
        ladder = [float(value) for value in swarm_cfg.get("size_ladder_usd", [])]
        if not ladder:
            return None
        probe_usd = float(swarm_cfg.get("multidex_probe_usd", ladder[0]))
        probe = evaluate(probe_usd)
        if probe is None:
            return None
        evaluated = [probe]
        if probe[1].positive:
            for usd_size in ladder:
                if usd_size == probe_usd:
                    continue
                item = evaluate(usd_size)
                if item is not None:
                    evaluated.append(item)
        quoted, economics, loan_raw = max(
            evaluated, key=lambda item: item[1].expected_net_usd)

        positive = economics.positive
        adapters_ready = all(
            bool(getattr(self.venues.get(leg.pool.venue_id),
                         "execution_supported", False))
            for leg in route.legs
        )
        executable = bool(route_config.get("executable", False)) and adapters_ready
        decision = "PASS" if positive and executable else (
            "OBSERVE" if positive else "REJECT")
        scale = 10 ** base_decimals
        loan_size = loan_raw / scale
        gross_base = economics.gross_raw / scale
        flash_fee_base = economics.flash_fee_raw / scale
        expected_net = float(economics.expected_net_usd)
        one_raw = 1 / scale
        min_profit = ((float(context.gas_usd) + float(model_reserve_usd))
                      / base_price + one_raw)
        candidate = None
        if positive:
            candidate = {
                "block": block,
                "route_id": route_config.get("route_id", route.id),
                "route_kind": "multihop_exact",
                "base_asset": route_config["base"],
                "loan_size": loan_size,
                "predicted_net": expected_net,
                "min_profit": min_profit,
                "executable": executable,
                "legs": route_config["legs"],
                "amount_out_raw": int(quoted.amount_out_raw),
                "quoter_gas_estimate": int(quoted.gas_estimate),
            }
        return {
            "block": block,
            "route_id": route_config.get("route_id", route.id),
            "name": route_config.get("name", "multi-hop"),
            "size": loan_size,
            "loan_notional_usd": loan_size * base_price,
            "gross": gross_base,
            "gross_usd": float(economics.gross_usd),
            "net": expected_net,
            "expected_net_usd": expected_net,
            "quote_source": "route_exact",
            "decision": decision,
            "reason": ("ok" if decision == "PASS" else
                       "execution_encoder_unavailable" if decision == "OBSERVE" else
                       "expected_net_usd <= 0"),
            "flash_premium_bps": int(context.premium_bps),
            "flash_fee": flash_fee_base,
            "flash_fee_usd": float(economics.flash_fee_usd),
            "gas_usd": float(context.gas_usd),
            "model_reserve_usd": float(model_reserve_usd),
            "min_profit": min_profit,
            "candidate": candidate,
        }

    def scan_multidex_route(self, block: int, route_config: dict, *,
                            model_reserve_usd: float = 0.0,
                            context: ScanContext | None = None) -> dict | None:
        """Exact-quote one two-leg venue route at a single pinned block."""
        block = int(block)
        if int(route_config.get("block", block)) != block:
            raise ValueError("mixed-block multi-dex route")
        if context is None or int(context.block) != block:
            raise ValueError("multi-dex scan requires a pinned ScanContext")
        leg1 = PoolRef(**route_config["leg1"])
        leg2 = PoolRef(**route_config["leg2"])
        route = TwoLegRoute(int(self.config.get("chain_id", 42161)), block,
                            route_config["base"], route_config["quote"],
                            leg1, leg2)
        base_decimals = int(route_config["base_decimals"])
        quote_decimals = int(route_config["quote_decimals"])
        base_price = float(route_config.get("base_price_usd", 0.0) or 0.0)
        symbol = route_config.get("base_symbol")
        if symbol in context.tokens:
            base_price = float(context.tokens[symbol].price_usd)
        if base_price <= 0:
            raise ValueError("multi-dex base price must be positive")

        def evaluate(usd_size: float) -> dict | None:
            loan_raw = int((Decimal(str(usd_size)) / Decimal(str(base_price))
                            * (Decimal(10) ** base_decimals)).to_integral_value(
                                rounding=ROUND_FLOOR))
            if loan_raw <= 0:
                return None
            try:
                q1 = quote_leg(self.venues, leg1, loan_raw,
                               route_config["base"], block)
                if int(q1.amount_out) <= 0:
                    return None
                q2 = quote_leg(self.venues, leg2, int(q1.amount_out),
                               route_config["quote"], block)
            except RpcError as exc:
                if str(exc).startswith("RPC error:"):
                    return None
                raise
            gross_raw = int(q2.amount_out) - loan_raw
            gross_base = gross_raw / (10 ** base_decimals)
            gross_usd = gross_base * base_price
            fee_raw = (loan_raw * int(context.premium_bps) + 5_000) // 10_000
            flash_fee_base = fee_raw / (10 ** base_decimals)
            flash_fee_usd = flash_fee_base * base_price
            expected = swarm_expected_net(gross_usd, flash_fee_usd,
                                          float(context.gas_usd),
                                          float(model_reserve_usd))
            return {
                "loan_raw": loan_raw, "size": loan_raw / (10 ** base_decimals),
                "hop1_out": int(q1.amount_out) / (10 ** quote_decimals),
                "hop2_out": int(q2.amount_out) / (10 ** base_decimals),
                "gross": gross_base, "gross_usd": gross_usd,
                "flash_fee_base": flash_fee_base, "flash_fee_usd": flash_fee_usd,
                "expected_net_usd": expected,
                "fee1": q1.fee_used if q1.fee_used is not None else leg1.fee_tier,
                "fee2": q2.fee_used if q2.fee_used is not None else leg2.fee_tier,
            }

        swarm_cfg = self.config.get("swarm", {})
        ladder = [float(v) for v in swarm_cfg.get("size_ladder_usd", [])]
        if not ladder:
            return None
        probe_usd = float(swarm_cfg.get("multidex_probe_usd", ladder[0]))
        probe = evaluate(probe_usd)
        if probe is None:
            return None
        evaluated = [probe]
        if float(probe["expected_net_usd"]) > 0:
            for usd_size in ladder:
                if usd_size == probe_usd:
                    continue
                item = evaluate(usd_size)
                if item is not None:
                    evaluated.append(item)
        best = max(evaluated, key=lambda item: item["expected_net_usd"])
        executable = route_execution_ready(route, self.venues)
        positive = float(best["expected_net_usd"]) > 0
        decision = "PASS" if positive and executable else ("OBSERVE" if positive else "REJECT")
        one_raw = 1 / (10 ** base_decimals)
        min_profit = ((float(context.gas_usd) + float(model_reserve_usd))
                      / base_price + one_raw)
        candidate = None
        if decision == "PASS":
            candidate = ArbitrageCandidate(
                block=block, name=route_config.get("name", "multi-dex"),
                base_asset=route_config["base"], quote_asset=route_config["quote"],
                base_decimals=base_decimals, quote_decimals=quote_decimals,
                loan_size=best["size"], hop1_expected_out=best["hop1_out"],
                hop2_expected_out=best["hop2_out"], fee1=int(best["fee1"] or 0),
                fee2=int(best["fee2"] or 0), flash_premium_bps=int(context.premium_bps),
                gas_cost_usd=float(context.gas_usd), gross_profit=best["gross"],
                predicted_net=best["expected_net_usd"], min_profit=min_profit)
        return {
            "block": block, "route_id": route_config.get("route_id", route.id),
            "name": route_config.get("name", "multi-dex"), "size": best["size"],
            "loan_notional_usd": best["size"] * base_price,
            "gross": best["gross"], "gross_usd": best["gross_usd"],
            "net": best["expected_net_usd"],
            "expected_net_usd": best["expected_net_usd"],
            "quote_source": "venue_exact", "decision": decision,
            "reason": "ok" if decision == "PASS" else (
                "execution_encoder_unavailable" if decision == "OBSERVE"
                else "expected_net_usd <= 0"),
            "flash_premium_bps": int(context.premium_bps),
            "flash_fee": best["flash_fee_base"],
            "flash_fee_usd": best["flash_fee_usd"],
            "hop1_out": best["hop1_out"], "hop2_out": best["hop2_out"],
            "gas_usd": float(context.gas_usd),
            "model_reserve_usd": float(model_reserve_usd),
            "min_profit": min_profit,
            "candidate": candidate.as_dict() if candidate else None,
        }

    def scan_cycle_config(self, block: int, cycle_config: dict, *,
                          sizes: list[float] | None = None,
                          swarm_mode: bool = False,
                          model_reserve_usd: float = 0.0,
                          context: ScanContext | None = None) -> dict | None:
        """Scan exactly one two-pool cycle, optionally from a frozen context.

        Legacy mode preserves v0.4 semantics. Swarm mode interprets the size
        ladder in USD, converts it to base-token units, and ranks every size by
        expected USD net after Aave premium, gas, and model reserve.
        """
        block = int(block)
        route_block = int(cycle_config.get("block", block))
        if route_block != block:
            raise ValueError("mixed-block scan context")
        if context is not None and int(context.block) != block:
            raise ValueError("mixed-block scan context")

        pools = [self._pool(pc, block=block) for pc in cycle_config["pools"]]
        if context is None:
            pool_address = self.aave.pool_address(block=block)
            oracle = self.aave.oracle_address(block=block)
            premium_bps = self.aave.flashloan_premium_total(
                pool_address, block=block)
            eth_asset = self.config["arbitrage"]["eth_for_gas"]
            eth_price = self.aave.asset_price(oracle, eth_asset, block=block)
            gas_usd = self._gas_cost_usd(eth_price)
            states = []
            for pool in pools:
                states.append(pool.fetch_state(block=block))
        else:
            premium_bps = int(context.premium_bps)
            gas_usd = float(context.gas_usd)
            states = []
            for pool in pools:
                address = pool.address.lower()
                if address not in context.pool_states:
                    raise ValueError(f"pool state missing from scan context: {address}")
                states.append(context.pool_states[address])

        cycle = Cycle(
            pools[0], pools[1], cycle_config["base"],
            cycle_config["base_decimals"], cycle_config["quote_decimals"])

        if swarm_mode:
            base_symbol = cycle_config.get("base_symbol")
            base_price_usd = None
            if context is not None and base_symbol in context.tokens:
                base_price_usd = context.tokens[base_symbol].price_usd
            if base_price_usd is None:
                base_price_usd = cycle_config.get("base_price_usd")
            base_price_usd = float(base_price_usd or 0.0)
            if base_price_usd <= 0:
                raise ValueError("base asset requires a positive pinned USD price")
            usd_sizes = sizes if sizes is not None else self.config.get(
                "swarm", {}).get("size_ladder_usd", [])
            base_sizes = [float(usd) / base_price_usd for usd in usd_sizes]
            curve = cycle.profit_curve(states[0], states[1], base_sizes)
            valid = [row for row in curve
                     if not row.get("out_of_range", False)
                     and math.isfinite(float(row.get("gross", float("-inf"))))]
            if not valid:
                return None

            evaluated = []
            for row in valid:
                gross_base = float(row["gross"])
                gross_usd = gross_base * base_price_usd
                flash_fee_base = float(row["size"]) * premium_bps / 10_000
                flash_fee_usd = flash_fee_base * base_price_usd
                expected_net_usd = swarm_expected_net(
                    gross_usd, flash_fee_usd, gas_usd, model_reserve_usd)
                item = dict(row)
                item.update({
                    "gross_usd": gross_usd,
                    "flash_fee_base": flash_fee_base,
                    "flash_fee_usd": flash_fee_usd,
                    "expected_net_usd": expected_net_usd,
                })
                evaluated.append(item)
            best = max(evaluated, key=lambda item: item["expected_net_usd"])
            local_expected_net_usd = float(best["expected_net_usd"])
            quote_source = "local_single_range"

            if (local_expected_net_usd > 0
                    and self.config.get("swarm", {}).get(
                        "exact_quoter_preflight", False)):
                best = self._exact_swarm_preflight(
                    block=block,
                    cycle_config=cycle_config,
                    best=best,
                    base_price_usd=base_price_usd,
                    premium_bps=premium_bps,
                    gas_usd=gas_usd,
                    model_reserve_usd=float(model_reserve_usd),
                )
                quote_source = "quoter_v2"

            expected_net_usd = float(best["expected_net_usd"])
            decision = "PASS" if expected_net_usd > 0 else "REJECT"
            if decision == "PASS":
                reason = "ok"
            elif quote_source == "quoter_v2" and local_expected_net_usd > 0:
                reason = "exact_quoter_net_usd <= 0"
            else:
                reason = "expected_net_usd <= 0"

            one_raw = 1 / (10 ** int(cycle_config["base_decimals"]))
            min_profit_base = (
                (gas_usd + float(model_reserve_usd)) / base_price_usd
                + one_raw
            )
            candidate = None
            if decision == "PASS":
                candidate = self.normalize_candidate(
                    block=block,
                    cycle_config=cycle_config,
                    best=best,
                    premium_bps=premium_bps,
                    gas_usd=gas_usd,
                    min_profit=min_profit_base,
                    predicted_net=expected_net_usd,
                )
            return {
                "block": block,
                "route_id": cycle_config.get("route_id"),
                "name": cycle_config.get("name", "?"),
                "size": best["size"],
                "loan_notional_usd": float(best["size"]) * base_price_usd,
                "gross": best["gross"],
                "gross_usd": best["gross_usd"],
                "net": expected_net_usd,
                "expected_net_usd": expected_net_usd,
                "local_expected_net_usd": local_expected_net_usd,
                "quote_source": quote_source,
                "decision": decision,
                "reason": reason,
                "flash_premium_bps": premium_bps,
                "flash_fee": best["flash_fee_base"],
                "flash_fee_usd": best["flash_fee_usd"],
                "hop1_out": best["hop1_out"],
                "hop2_out": best["hop2_out"],
                "gas_usd": gas_usd,
                "model_reserve_usd": float(model_reserve_usd),
                "min_profit": min_profit_base,
                "candidate": candidate.as_dict() if candidate else None,
            }

        scan_sizes = sizes
        if scan_sizes is None:
            cfg = self.config["arbitrage"]
            scan_sizes = sweep_sizes(cfg["min_size"], cfg["max_size"])
        curve = cycle.profit_curve(states[0], states[1], scan_sizes)
        best = best_opportunity(curve)
        if best is None:
            return None
        gross = best["gross"]
        economics = self.flash_economics(
            gross=gross, loan_size=best["size"],
            premium_bps=premium_bps, gas_usd=gas_usd)
        flash_fee = economics["flash_fee"]
        net = economics["net"]
        decision, reason, min_profit = self.config["_gate"].evaluate(
            net, gross, gas_usd, best["size"])
        candidate = None
        if decision == "PASS":
            candidate = self.normalize_candidate(
                block=block,
                cycle_config=cycle_config,
                best=best,
                premium_bps=premium_bps,
                gas_usd=gas_usd,
                min_profit=min_profit,
            )
        return {
            "block": block,
            "route_id": cycle_config.get("route_id"),
            "name": cycle_config.get("name", "?"),
            "size": best["size"],
            "gross": gross,
            "net": net,
            "decision": decision,
            "reason": reason,
            "flash_premium_bps": premium_bps,
            "flash_fee": flash_fee,
            "hop1_out": best["hop1_out"],
            "hop2_out": best["hop2_out"],
            "gas_usd": gas_usd,
            "min_profit": min_profit,
            "candidate": candidate.as_dict() if candidate else None,
        }

    def scan_arbitrage(self, block: int) -> dict:
        """Sweep every configured cycle; gate the best size per cycle."""
        cfg = self.config["arbitrage"]
        results = {"cycles": [], "candidates": [], "detected": 0,
                   "passed": 0, "rejected": 0}
        for cyc_cfg in cfg["cycles"]:
            row = self.scan_cycle_config(block, cyc_cfg)
            if row is None:
                continue
            results["detected"] += 1
            candidate = row.get("candidate")
            if candidate is not None:
                results["candidates"].append(candidate)
            detail = {
                "tokens": [p["token0"] for p in cyc_cfg["pools"]]
                          + [cyc_cfg["pools"][0]["token1"]],
                "gas_usd": row["gas_usd"],
                "flash_premium_bps": row["flash_premium_bps"],
                "flash_fee": row["flash_fee"],
                "hop1_out": row["hop1_out"],
                "hop2_out": row["hop2_out"],
            }
            if candidate is not None:
                detail["candidate"] = candidate
            self.ledger.record(
                block=block, strategy="arbitrage", decision=row["decision"],
                asset=cyc_cfg["base"], loan_size=row["size"],
                gross=row["gross"], net=row["net"],
                min_profit=row["min_profit"], reason=row["reason"],
                detail=detail)
            if row["decision"] == "PASS":
                results["passed"] += 1
            else:
                results["rejected"] += 1
            results["cycles"].append(row)
        return results

    def scan_liquidations(self, block: int) -> dict:
        cfg = self.config["liquidation"]
        if not cfg.get("watchlist"):
            return {"records": [], "detected": 0, "passed": 0, "rejected": 0}
        pool = self.aave.pool_address()
        records = scan_watchlist(self.aave, self.rpc, pool,
                                 cfg["watchlist"], cfg.get("bonus", 0.05))
        oracle = self.aave.oracle_address()
        eth_price = self.aave.asset_price(oracle, cfg["eth_for_gas"])
        gas_usd = self._gas_cost_usd(eth_price)
        decisions = gate_liquidations(records, self.config["_gate"], gas_usd,
                                      discovered_at_ms=time.time() * 1000)
        for d in decisions:
            self.ledger.record(
                block=block, strategy="liquidation", decision=d["decision"],
                asset=None, loan_size=None, gross=d["gross"], net=d["net"],
                min_profit=None, reason=d["reason"], detail=d)
        passed = sum(1 for d in decisions if d["decision"] == "PASS")
        return {"records": records, "detected": len(decisions),
                "passed": passed, "rejected": len(decisions) - passed}

    def _pool(self, pcfg: dict, block: int | str = "latest"):
        from .uniswap_v3 import UniswapV3Pool
        if pcfg.get("address"):
            pool_addr = pcfg["address"]
        else:
            factory = self.config["uniswap_v3_factory"]
            data = (self.config["_sel_getpool"]
                    + self.config["_enc"](pcfg["token0"])[2:]
                    + self.config["_enc"](pcfg["token1"])[2:]
                    + self.config["_enc_uint"](pcfg["fee_tier"])[2:])
            raw = self.rpc.eth_call(factory, data, block=block)
            addr_int = int.from_bytes(raw[:32], "big")
            pool_addr = "0x" + addr_int.to_bytes(20, "big").hex()
        return UniswapV3Pool(
            self.rpc, pool_addr, pcfg["token0"], pcfg["token1"],
            pcfg["fee_percent"], pcfg["decimals0"], pcfg["decimals1"])

    def run_once(self) -> dict:
        block = self.rpc.block_number()
        t0 = time.time()
        arb = self.scan_arbitrage(block)
        liq = self.scan_liquidations(block)
        self.ledger.record_cycle(
            block=block, detected=arb["detected"] + liq["detected"],
            passed=arb["passed"] + liq["passed"],
            rejected=arb["rejected"] + liq["rejected"],
            note=f"cycle took {time.time() - t0:.2f}s")
        return {"block": block, "arbitrage": arb, "liquidations": liq,
                "elapsed_s": time.time() - t0}
