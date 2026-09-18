from __future__ import annotations

import time
from decimal import Decimal

from .keccak import keccak256
from .liquidation_quote import UnwindRoute, quote_liquidation
from .liquidations import build_liquidation_state
from .routes import RouteLeg
from .swarm import SwarmCandidate


def _route_payload(route: UnwindRoute) -> list[dict]:
    return [{
        'pool': leg.pool.__dict__.copy(),
        'token_in': leg.token_in,
        'token_out': leg.token_out,
    } for leg in route.legs]


def scan_liquidation_watchlist(engine, config: dict, context,
                               venue_registry: dict[str, object], *,
                               model_reserve_usd: float = 0.0):
    from .keccak import selector_hex
    from .rpc import RpcError, decode_uints, encode_address

    liq_cfg = config.get('liquidation', {}) or {}
    raw_borrowers = liq_cfg.get('borrowers') or liq_cfg.get('watchlist') or []
    borrowers = []
    seen = set()
    for value in raw_borrowers:
        borrower = str(value).lower()
        if borrower in seen:
            continue
        seen.add(borrower)
        borrowers.append(borrower)

    # Cheap pinned batch of getUserAccountData for the whole watchlist: only
    # borrowers inside the hot band get the full multi-call state build, so
    # an idle watchlist costs one batch instead of one scan per borrower.
    hot_band = float(liq_cfg.get('pre_filter_hf', 1.0))
    pool = engine.aave.pool_address(block=context.block)
    data_calls = [(pool, selector_hex('getUserAccountData(address)')
                   + encode_address(b)[2:]) for b in borrowers]
    hfs: dict[str, float | None] = {b: None for b in borrowers}
    if data_calls:
        try:
            for borrower, data in zip(borrowers, engine.rpc.
                                      batch_eth_call_results(
                data_calls, block=context.block)):
                if isinstance(data, RpcError) or not data:
                    continue
                words = decode_uints(data)
                if len(words) >= 6 and words[5] > 0:
                    hfs[borrower] = words[5] / 1e18
        except Exception:
            # Pre-filter failed: fall back to scanning everyone below.
            hfs = {b: None for b in borrowers}
    borrowers = [b for b in borrowers
                 if hfs[b] is None or hfs[b] < hot_band]

    candidates: list[SwarmCandidate] = []
    errors: list[dict] = []
    for borrower in borrowers:
        try:
            state = build_liquidation_state(engine.aave, borrower, context.block)
            if not state.liquidatable:
                continue
            best = None
            for collateral in state.positions:
                if collateral.collateral_raw <= 0 or not collateral.usage_as_collateral_enabled:
                    continue
                for debt in state.positions:
                    if debt.debt_raw <= 0 or debt.asset == collateral.asset:
                        continue
                    for adapter in venue_registry.values():
                        if not bool(getattr(adapter, 'exact_quote_supported', False)):
                            continue
                        if not bool(getattr(adapter, 'execution_supported', False)):
                            continue
                        for pool in adapter.discover_pair(
                                collateral.asset, debt.asset, context.block):
                            route = UnwindRoute((RouteLeg(
                                pool, collateral.asset, debt.asset),))
                            candidate = quote_liquidation(
                                state, debt.asset, collateral.asset, route,
                                context.block, venue_registry,
                                flash_premium_bps=int(context.premium_bps),
                                gas_usd=Decimal(str(context.gas_usd)),
                                reserve_usd=Decimal(str(model_reserve_usd)))
                            if candidate is None:
                                continue
                            if best is None or candidate.expected_net_usd > best.expected_net_usd:
                                best = candidate
            if best is None:
                continue
            debt = state.position(best.debt_asset)
            scale = Decimal(10) ** debt.decimals
            loan_size = float(Decimal(best.debt_to_cover_raw) / scale)
            notional = float(Decimal(best.debt_to_cover_raw) / scale * debt.price_usd)
            route_text = '|'.join(leg.pool.id for leg in best.route.legs)
            route_id = '0x' + keccak256(
                f'liq|{context.block}|{borrower}|{best.debt_asset}|{best.collateral_asset}|{route_text}'.encode()
            ).hex()
            candidate_id = '0x' + keccak256(
                f'{route_id}|{best.debt_to_cover_raw}'.encode()).hex()
            payload = {
                'strategy': 'swarm_liquidation',
                'route_kind': 'liquidation_exact',
                'executable': True,
                'block': context.block,
                'borrower': borrower,
                'base_asset': best.debt_asset,
                'base_decimals': int(debt.decimals),
                'base_price_usd': float(debt.price_usd),
                'gas_cost_usd': float(best.gas_usd),
                'collateral_asset': best.collateral_asset,
                'loan_size': loan_size,
                'debt_to_cover_raw': best.debt_to_cover_raw,
                'collateral_received_raw': best.collateral_received_raw,
                'unwind_out_raw': best.unwind_out_raw,
                'flash_fee_raw': best.flash_fee_raw,
                'min_profit_raw': best.min_profit_raw,
                'predicted_net': float(best.expected_net_usd),
                'min_profit': float(Decimal(best.min_profit_raw) / scale),
                'legs': _route_payload(best.route),
            }
            candidates.append(SwarmCandidate(
                candidate_id=candidate_id,
                route_id=route_id,
                worker_id='LIQ-1',
                manager_id='LIQUIDATION',
                block=int(context.block),
                loan_size=loan_size,
                gross_profit=float(best.gross_usd),
                flash_fee=float(best.flash_fee_usd),
                gas_cost=float(best.gas_usd),
                model_reserve=float(best.reserve_usd),
                expected_net=float(best.expected_net_usd),
                roi=(float(best.expected_net_usd) / notional) if notional > 0 else 0.0,
                timestamp=time.time(),
                payload={'candidate': payload, 'route': {
                    'route_kind': 'liquidation_exact',
                    'borrower': borrower,
                    'legs': payload['legs'],
                }},
            ))
        except Exception as exc:
            errors.append({
                'block': int(context.block),
                'borrower': borrower,
                'error': f'{type(exc).__name__}: {exc}',
            })
    return candidates, errors
