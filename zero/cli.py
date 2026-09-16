"""ZERO Engine CLI — shadow + local fork verification.

    python3 -m zero.cli doctor
    python3 -m zero.cli prices
    python3 -m zero.cli hf 0xADDR
    python3 -m zero.cli scan
    python3 -m zero.cli candidate
    python3 -m zero.cli calldata
    python3 -m zero.cli fork-arb
    python3 -m zero.cli swarm-once
    python3 -m zero.cli swarm [--interval 5]
    python3 -m zero.cli shadow [--once]
    python3 -m zero.cli ledger [--tail 20]
    python3 -m zero.cli fork-status [--block N]
    python3 -m zero.cli fork-command [--block N]
    python3 -m zero.cli fork-test [--block N]
    python3 -m zero.cli fork-ledger [--tail 20]
"""

import argparse
from dataclasses import fields
import json
import os
import sys
import time

from .aave import AaveV3, bucket_for
from .calldata import build_uniswap_v3_steps
from .candidate import ArbitrageCandidate
from .engine import ShadowEngine
from .fork_cli import (
    build_status,
    command_line,
    run_fork_test,
    run_live_candidate_fork,
    run_live_candidate_fork_result,
    run_live_liquidation_fork_result,
)
from .gate import Gate
from .keccak import selector_hex
from .ledger import Ledger
from .pnl import PnlSwarmSupervisor
from .rpc import Rpc, encode_address, encode_uint, to_checksum
from .runtime import RuntimeMonitor

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config",
                           "arbitrum.json")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    rpc_urls_env = os.environ.get("ZERO_RPC_URLS", "").strip()
    rpc_url_env = os.environ.get("ZERO_RPC_URL", "").strip()
    if rpc_urls_env:
        rpc_urls = [value.strip() for value in rpc_urls_env.split(",")
                    if value.strip()]
        if not rpc_urls:
            raise ValueError("ZERO_RPC_URLS must contain at least one URL")
        cfg["rpc_urls"] = rpc_urls
        cfg["rpc_url"] = rpc_urls[0]
    elif rpc_url_env:
        cfg["rpc_url"] = rpc_url_env
        cfg["rpc_urls"] = [rpc_url_env]
    else:
        cfg.setdefault("rpc_urls", [cfg["rpc_url"]])
    if os.environ.get("ZERO_RPC_COOLDOWN_S"):
        cfg["rpc_cooldown_s"] = float(os.environ["ZERO_RPC_COOLDOWN_S"])
    if os.environ.get("ZERO_LEDGER_PATH"):
        cfg["ledger_path"] = os.environ["ZERO_LEDGER_PATH"]
    if os.environ.get("ZERO_MAX_RPC_BATCH"):
        cfg.setdefault("swarm", {})["max_rpc_batch"] = int(
            os.environ["ZERO_MAX_RPC_BATCH"])
    if os.environ.get("ZERO_MAX_RPC_CONCURRENCY"):
        cfg.setdefault("swarm", {})["max_rpc_concurrency"] = int(
            os.environ["ZERO_MAX_RPC_CONCURRENCY"])
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


def _run_swarm_verifier(cfg: dict, payload: dict):
    if payload.get("kind") == "liquidation":
        return run_live_liquidation_fork_result(cfg["rpc_url"], payload)
    return run_live_candidate_fork_result(cfg["rpc_url"], payload)


def _swarm_supervisor(ledger_path: str | None = None) -> PnlSwarmSupervisor:
    cfg = load_config()
    path = ledger_path or cfg.get("ledger_path", "zero_ledger.db")
    ledger = Ledger(path)
    engine = ShadowEngine(cfg["rpc_url"], cfg, ledger)

    def verifier(payload: dict):
        return _run_swarm_verifier(cfg, payload)

    return PnlSwarmSupervisor(engine, cfg, ledger, verifier=verifier)


def _runtime_monitor(cfg: dict) -> RuntimeMonitor:
    path = (os.environ.get("ZERO_RUNTIME_STATUS_PATH", "").strip()
            or cfg.get("runtime", {}).get("status_path", "run/zero-status.json"))
    return RuntimeMonitor(path)


def _runtime_endpoint(supervisor) -> str | None:
    rpc = getattr(getattr(supervisor, "engine", None), "rpc", None)
    if rpc is None:
        return None
    return getattr(rpc, "current_endpoint", getattr(rpc, "url", None))


def _record_runtime_cycle(supervisor, monitor: RuntimeMonitor, result: dict):
    head = int(result.get("block", 0) or 0)
    try:
        head = int(supervisor.engine.rpc.block_number())
    except Exception:
        pass
    return monitor.record_cycle(
        result, chain_head=head, rpc_endpoint=_runtime_endpoint(supervisor))


def _fork_block(cfg: dict, requested: int | None) -> int:
    if requested is not None:
        return requested
    return Rpc(cfg["rpc_url"]).block_number()


def _candidate_from_dict(data: dict) -> ArbitrageCandidate:
    names = {f.name for f in fields(ArbitrageCandidate)}
    return ArbitrageCandidate(**{name: data[name] for name in names})


def build_candidate_calldata(result: dict, cfg: dict) -> dict | None:
    candidates = result.get("arbitrage", {}).get("candidates", [])
    if not candidates:
        return None
    best_dict = max(candidates, key=lambda c: c["predicted_net"])
    candidate = _candidate_from_dict(best_dict)
    execution = cfg["arbitrage"]["execution"]
    steps = build_uniswap_v3_steps(
        candidate,
        execution["swap_router_02"],
        slippage_bps=int(execution.get("slippage_bps", 20)),
    )
    return {
        "candidate": candidate.as_dict(),
        "steps": [step.as_dict() for step in steps],
    }


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
    print(json.dumps(_engine().run_once(), indent=2, default=str))


def cmd_candidate(args):
    result = _engine().run_once()
    print(json.dumps(result["arbitrage"].get("candidates", []), indent=2))


def cmd_calldata(args):
    cfg = load_config()
    result = _engine().run_once()
    payload = build_candidate_calldata(result, cfg)
    if payload is None:
        print(json.dumps({"candidate": None, "steps": [],
                          "reason": "no_pass_candidate"}, indent=2))
        return 3
    print(json.dumps(payload, indent=2))
    return 0


def cmd_fork_arb(args):
    cfg = load_config()
    result = _engine().run_once()
    payload = build_candidate_calldata(result, cfg)
    if payload is None:
        print(json.dumps({"fork_verified": False,
                          "reason": "no_pass_candidate"}, indent=2))
        return 3
    candidate = payload["candidate"]
    print(json.dumps({
        "mode": "exact-block-fork-only",
        "candidate": candidate,
        "step_count": len(payload["steps"]),
        "mainnet_broadcast": False,
    }, indent=2))
    return run_live_candidate_fork(cfg["rpc_url"], payload)


def cmd_swarm_once(args):
    supervisor = _swarm_supervisor(getattr(args, "ledger", None))
    cfg = getattr(supervisor, "config", None) or load_config()
    monitor = _runtime_monitor(cfg)
    result = supervisor.run_block()
    _record_runtime_cycle(supervisor, monitor, result)
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_swarm(args):
    supervisor = _swarm_supervisor(getattr(args, "ledger", None))
    cfg = getattr(supervisor, "config", None) or load_config()
    monitor = _runtime_monitor(cfg)
    while True:
        try:
            result = supervisor.run_block()
            _record_runtime_cycle(supervisor, monitor, result)
            print(
                f"block {result['block']} "
                f"workers={result['active_workers']} "
                f"routes={result['routes_scanned']} "
                f"positive={result['positive_net']} "
                f"best_net={result['best_expected_net']} "
                f"fork={result['fork_verifications_passed']}/"
                f"{result['fork_verifications_attempted']} "
                f"failures={result['worker_failures']} "
                f"({result['elapsed_s']:.2f}s)"
            )
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            monitor.record_error(exc, rpc_endpoint=_runtime_endpoint(supervisor))
            print(f"swarm cycle error: {type(exc).__name__}: {exc}")
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


def cmd_health(args):
    path = getattr(args, "status", None)
    if not path:
        cfg = load_config()
        path = (os.environ.get("ZERO_RUNTIME_STATUS_PATH", "").strip()
                or cfg.get("runtime", {}).get("status_path", "run/zero-status.json"))
    max_age = max(0.0, float(getattr(args, "max_age", 120.0)))
    if not os.path.exists(path):
        print(json.dumps({"healthy": False, "reason": "status_missing",
                          "status_path": path}, indent=2))
        return 2
    try:
        with open(path) as handle:
            status = json.load(handle)
    except Exception as exc:
        print(json.dumps({"healthy": False, "reason": "status_invalid",
                          "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2
    heartbeat = float(status.get("heartbeat_at", 0.0) or 0.0)
    age = max(0.0, time.time() - heartbeat)
    killed = bool(status.get("kill_state", False))
    healthy = (not killed) and heartbeat > 0 and age <= max_age
    reason = ("kill_state" if killed else
              "heartbeat_stale" if heartbeat <= 0 or age > max_age else "ok")
    out = dict(status)
    out.update({"healthy": healthy, "reason": reason,
                "heartbeat_age_s": age, "status_path": path})
    print(json.dumps(out, indent=2, default=str))
    return 0 if healthy else (3 if killed else 2)


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


def cmd_fork_status(args):
    cfg = load_config()
    block = _fork_block(cfg, args.block)
    status = build_status(cfg["rpc_url"], block, args.port)
    print(json.dumps(status, indent=2))
    return 0 if status["anvil_installed"] else 2


def cmd_fork_command(args):
    cfg = load_config()
    block = _fork_block(cfg, args.block)
    print(command_line(cfg["rpc_url"], block, args.port))


def cmd_fork_test(args):
    cfg = load_config()
    block = _fork_block(cfg, args.block)
    print(f"Fork verification only — block {block}; no mainnet writes.")
    return run_fork_test(cfg["rpc_url"], block)


def cmd_fork_ledger(args):
    cfg = load_config()
    path = args.ledger or cfg.get("ledger_path", "zero_ledger.db")
    ledger = Ledger(path)
    try:
        print(json.dumps(ledger.fork_tail(args.tail), indent=2))
    finally:
        ledger.close()


def _add_fork_block_args(parser):
    parser.add_argument("--block", type=int, default=None,
                        help="Arbitrum block to pin; defaults to current block")
    parser.add_argument("--port", type=int, default=8545,
                        help="local Anvil port (status/command only)")


def main(argv=None):
    p = argparse.ArgumentParser(prog="zero",
                                description="ZERO Engine shadow + local fork verification")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("prices").set_defaults(func=cmd_prices)
    hf = sub.add_parser("hf", help="health factor of one address")
    hf.add_argument("address")
    hf.set_defaults(func=cmd_hf)
    sub.add_parser("scan").set_defaults(func=cmd_scan)
    sub.add_parser("candidate", help="print current PASS arbitrage candidates").set_defaults(func=cmd_candidate)
    sub.add_parser("calldata", help="encode best PASS candidate for fork replay").set_defaults(func=cmd_calldata)
    sub.add_parser("fork-arb", help="scan and replay best PASS candidate on its exact fork block").set_defaults(func=cmd_fork_arb)

    so = sub.add_parser("swarm-once", help="run one 20-worker exact-block swarm cycle")
    so.add_argument("--ledger", default=None)
    so.set_defaults(func=cmd_swarm_once)
    sw = sub.add_parser("swarm", help="run the 20-worker swarm continuously")
    sw.add_argument("--interval", type=float, default=5.0)
    sw.add_argument("--ledger", default=None)
    sw.set_defaults(func=cmd_swarm)
    health = sub.add_parser("health", help="read the always-on runtime heartbeat")
    health.add_argument("--status", default=None)
    health.add_argument("--max-age", type=float, default=120.0)
    health.set_defaults(func=cmd_health)

    sh = sub.add_parser("shadow", help="run shadow cycles (no signing)")
    sh.add_argument("--once", action="store_true")
    sh.add_argument("--interval", type=float, default=10.0)
    sh.set_defaults(func=cmd_shadow)
    lg = sub.add_parser("ledger")
    lg.add_argument("--tail", type=int, default=20)
    lg.add_argument("--ledger", default=None)
    lg.set_defaults(func=cmd_ledger)

    fs = sub.add_parser("fork-status", help="check local Anvil fork capability")
    _add_fork_block_args(fs)
    fs.set_defaults(func=cmd_fork_status)
    fc = sub.add_parser("fork-command", help="print exact-block Anvil command")
    _add_fork_block_args(fc)
    fc.set_defaults(func=cmd_fork_command)
    ft = sub.add_parser("fork-test", help="run real Aave flash-loan test on fork")
    ft.add_argument("--block", type=int, default=None)
    ft.set_defaults(func=cmd_fork_test)
    fl = sub.add_parser("fork-ledger", help="show stored fork verification results")
    fl.add_argument("--tail", type=int, default=20)
    fl.add_argument("--ledger", default=None)
    fl.set_defaults(func=cmd_fork_ledger)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
