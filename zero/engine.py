"""Shadow-mode engine: live chain data in, decisions recorded, nothing sent.

HARD INVARIANT: this module has no signer, no private key, and no code path
that broadcasts a transaction. Its output is the SQLite ledger and stdout.
"""

import time

from .aave import AaveV3
from .candidate import ArbitrageCandidate
from .rpc import Rpc
from .strategies.arbitrage import Cycle, best_opportunity, sweep_sizes
from .strategies.liquidation import gate_liquidations, scan_watchlist


class ShadowEngine:
    def __init__(self, rpc_url: str, config: dict, ledger):
        self.rpc = Rpc(rpc_url)
        self.config = config
        self.ledger = ledger
        self.aave = AaveV3(self.rpc, config["aave_provider"])
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
                            min_profit: float) -> ArbitrageCandidate:
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
        economics = ShadowEngine.flash_economics(
            gross=best["gross"], loan_size=best["size"],
            premium_bps=premium_bps, gas_usd=gas_usd)
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
            predicted_net=economics["net"],
            min_profit=min_profit,
        )

    def scan_arbitrage(self, block: int) -> dict:
        """Sweep every configured cycle; gate the best size per cycle."""
        cfg = self.config["arbitrage"]
        oracle = self.aave.oracle_address()
        pool_address = self.aave.pool_address()
        premium_bps = self.aave.flashloan_premium_total(pool_address)
        eth_price = self.aave.asset_price(oracle, cfg["eth_for_gas"])
        gas_usd = self._gas_cost_usd(eth_price)
        sizes = sweep_sizes(cfg["min_size"], cfg["max_size"])

        results = {"cycles": [], "candidates": [], "detected": 0,
                   "passed": 0, "rejected": 0}
        for cyc_cfg in cfg["cycles"]:
            pools = [self._pool(pc) for pc in cyc_cfg["pools"]]
            for pool in pools:
                pool.state = pool.fetch_state()
            cycle = Cycle(pools[0], pools[1], cyc_cfg["base"],
                          cyc_cfg["base_decimals"], cyc_cfg["quote_decimals"])
            curve = cycle.profit_curve(pools[0].state, pools[1].state, sizes)
            best = best_opportunity(curve)
            if best is None:
                continue
            results["detected"] += 1
            gross = best["gross"]
            economics = self.flash_economics(
                gross=gross,
                loan_size=best["size"],
                premium_bps=premium_bps,
                gas_usd=gas_usd,
            )
            flash_fee = economics["flash_fee"]
            net = economics["net"]
            decision, reason, min_profit = self.config["_gate"].evaluate(
                net, gross, gas_usd, best["size"])
            candidate = None
            if decision == "PASS":
                candidate = self.normalize_candidate(
                    block=block,
                    cycle_config=cyc_cfg,
                    best=best,
                    premium_bps=premium_bps,
                    gas_usd=gas_usd,
                    min_profit=min_profit,
                )
                results["candidates"].append(candidate.as_dict())
            detail = {
                "tokens": [p.token0 for p in pools] + [pools[0].token1],
                "gas_usd": gas_usd,
                "flash_premium_bps": premium_bps,
                "flash_fee": flash_fee,
                "hop1_out": best["hop1_out"],
                "hop2_out": best["hop2_out"],
            }
            if candidate is not None:
                detail["candidate"] = candidate.as_dict()
            self.ledger.record(
                block=block, strategy="arbitrage", decision=decision,
                asset=cyc_cfg["base"], loan_size=best["size"], gross=gross,
                net=net, min_profit=min_profit, reason=reason, detail=detail)
            if decision == "PASS":
                results["passed"] += 1
            else:
                results["rejected"] += 1
            cycle_row = {
                "name": cyc_cfg.get("name", "?"), "size": best["size"],
                "gross": gross, "net": net, "decision": decision,
                "reason": reason, "flash_premium_bps": premium_bps,
                "flash_fee": flash_fee, "hop1_out": best["hop1_out"],
                "hop2_out": best["hop2_out"], "min_profit": min_profit,
            }
            if candidate is not None:
                cycle_row["candidate"] = candidate.as_dict()
            results["cycles"].append(cycle_row)
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

    def _pool(self, pcfg: dict):
        from .uniswap_v3 import UniswapV3Pool
        factory = self.config["uniswap_v3_factory"]
        data = (self.config["_sel_getpool"]
                + self.config["_enc"](pcfg["token0"])[2:]
                + self.config["_enc"](pcfg["token1"])[2:]
                + self.config["_enc_uint"](pcfg["fee_tier"])[2:])
        raw = self.rpc.eth_call(factory, data)
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
