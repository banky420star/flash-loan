"""Shadow-mode engine: live chain data in, decisions recorded, nothing sent.

HARD INVARIANT: this module has no signer, no private key, and no code path
that broadcasts a transaction. Its output is the SQLite ledger and stdout.
"""

import time

from .aave import AaveV3
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

    def scan_arbitrage(self, block: int) -> dict:
        """Sweep every configured cycle; gate the best size per cycle."""
        cfg = self.config["arbitrage"]
        oracle = self.aave.oracle_address()
        eth_price = self.aave.asset_price(oracle, cfg["eth_for_gas"])
        gas_usd = self._gas_cost_usd(eth_price)
        sizes = sweep_sizes(cfg["min_size"], cfg["max_size"])

        results = {"cycles": [], "detected": 0, "passed": 0, "rejected": 0}
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
            net = gross - gas_usd
            decision, reason, min_profit = self.config["_gate"].evaluate(
                net, gross, gas_usd, best["size"])
            self.ledger.record(
                block=block, strategy="arbitrage", decision=decision,
                asset=cyc_cfg["base"], loan_size=best["size"], gross=gross,
                net=net, min_profit=min_profit, reason=reason,
                detail={"tokens": [p.token0 for p in pools] + [pools[0].token1],
                        "gas_usd": gas_usd})
            if decision == "PASS":
                results["passed"] += 1
            else:
                results["rejected"] += 1
            results["cycles"].append({
                "name": cyc_cfg.get("name", "?"), "size": best["size"],
                "gross": gross, "net": net, "decision": decision,
                "reason": reason})
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