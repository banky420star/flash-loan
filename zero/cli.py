"""ZERO Engine CLI — shadow mode only. No wallet, no signing, ever.

    python3 -m zero.cli doctor
    python3 -m zero.cli prices
    python3 -m zero.cli hf 0xADDR
    python3 -m zero.cli scan
    python3 -m zero.cli shadow [--once]
    python3 -m zero.cli ledger [--tail 20]
"""

import argparse
import json
import os
import sys
import time

from .aave import AaveV3, bucket_for
from .engine import ShadowEngine
from .gate import Gate
from .keccak import selector_hex
from .ledger import Ledger
from .rpc import Rpc, encode_address, encode_uint, to_checksum

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config",
                           "arbitrum.json")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    gate_cfg = cfg.get("gate", {})
    cfg["_gate"] = Gate(floor_usd=gate_cfg.get("floor_usd", 2.0),
                        gas_multiple=gate_cfg.get("gas_multiple", 4.0),
                        min_roi=gate_cfg.get("min_roi", 0.0001),
                        max_age_ms=gate_cfg.get("max_age_ms", 500))
    cfg["_sel_getpool"] = selector_hex("getPool(address,address,uint24)")
    cfg["_enc"] = encode_address
    cfg["_enc_uint"] = encode_uint
    return cfg


def _engine(ledger_path: str | None = None) -> ShadowEngine:
    cfg = load_config()
    path = ledger_path or cfg.get("ledger_path", "zero_ledger.db")
    return ShadowEngine(cfg["rpc_url"], cfg, Ledger(path))


def cmd_doctor(args):
    cfg = load_config()
    rpc = Rpc(cfg["rpc_url"])
    aave = AaveV3(rpc, cfg["aave_provider"])
    checks = [
        ("chain_id", rpc.chain_id() == cfg["chain_id"], rpc.chain_id()),
        ("block_height", rpc.block_number() > 0, rpc.block_number()),
        ("aave_provider_code", len(rpc.get_code(cfg["aave_provider"])) > 100,
         "deployed"),
        ("uniswap_factory_code",
         len(rpc.get_code(cfg["uniswap_v3_factory"])) > 100, "deployed"),
    ]
    pool, oracle = aave.pool_address(), aave.oracle_address()
    checks += [
        ("aave_pool_resolved", len(rpc.get_code(pool)) > 100, to_checksum(pool)),
        ("aave_oracle_resolved", len(rpc.get_code(oracle)) > 100,
         to_checksum(oracle)),
    ]
    ok = all(c[1] for c in checks)
    for name, good, detail in checks:
        print(f"  {'OK ' if good else 'FAIL'} {name:24s} {detail}")
    print(f"\nDOCTOR {'PASS' if ok else 'FAIL'} — "
          f"{'live data reachable' if ok else 'something is wrong'}")
    return 0 if ok else 1


def cmd_prices(args):
    """Prices derived on-chain: reserves list from the Pool, no hardcoding."""
    cfg = load_config()
    rpc = Rpc(cfg["rpc_url"])
    aave = AaveV3(rpc, cfg["aave_provider"])
    pool, oracle = aave.pool_address(), aave.oracle_address()
    assets = aave.reserves_list(pool)
    print(f"  {len(assets)} reserves from the pool registry:\n")
    for addr in assets:
        try:
            sym = aave.symbol(addr)
            price = aave.asset_price(oracle, addr)
            print(f"  {sym:8s} ${price:>12,.4f}")
        except Exception as e:
            sym = sym if 'sym' in dir() else addr[:10]
            print(f"  {sym:8s} (unavailable: {type(e).__name__})")


def cmd_hf(args):
    cfg = load_config()
    rpc = Rpc(cfg["rpc_url"])
    aave = AaveV3(rpc, cfg["aave_provider"])
    pool = aave.pool_address()
    d = aave.account_data(pool, args.address)
    print(f"  address      {to_checksum(args.address)}")
    print(f"  collateral   ${d['collateral_usd']:>14,.2f}")
    print(f"  debt         ${d['debt_usd']:>14,.2f}")
    print(f"  liq threshold {d['liquidation_threshold']:.4f}")
    print(f"  health factor {d['health_factor']:.6f}  [{bucket_for(d['health_factor'])}]")


def cmd_scan(args):
    eng = _engine()
    res = eng.run_once()
    print(json.dumps(res, indent=2, default=str))


def cmd_shadow(args):
    eng = _engine()
    while True:
        try:
            res = eng.run_once()
            arb, liq = res["arbitrage"], res["liquidations"]
            print(f"  block {res['block']}: "
                  f"arb {arb['detected']} detected / {arb['passed']} passed, "
                  f"liq {liq['detected']} detected / {liq['passed']} passed "
                  f"({res['elapsed_s']:.2f}s)")
        except Exception as e:
            print(f"  cycle error: {e}")
        if args.once:
            break
        time.sleep(args.interval)


def cmd_ledger(args):
    eng = _engine(args.ledger)
    stats = eng.ledger.stats()
    print("  stats by strategy:")
    for strategy, counts in stats.items():
        print(f"    {strategy}: {counts}")
    print(f"\n  last {args.tail}:")
    for row in eng.ledger.tail(args.tail):
        print(f"    {row['created_at']} {row['strategy']:11s} {row['decision']:6s} "
              f"size={row['loan_size']} net={row['net']} min={row['min_profit']} "
              f"{row['reason'] or ''}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="zero",
                                description="ZERO Engine shadow mode (read-only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("prices").set_defaults(func=cmd_prices)
    hf = sub.add_parser("hf", help="health factor of one address")
    hf.add_argument("address")
    hf.set_defaults(func=cmd_hf)
    sub.add_parser("scan").set_defaults(func=cmd_scan)
    sh = sub.add_parser("shadow", help="run shadow cycles (no signing)")
    sh.add_argument("--once", action="store_true")
    sh.add_argument("--interval", type=float, default=10.0)
    sh.set_defaults(func=cmd_shadow)
    lg = sub.add_parser("ledger")
    lg.add_argument("--tail", type=int, default=20)
    lg.add_argument("--ledger", default=None)
    lg.set_defaults(func=cmd_ledger)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)