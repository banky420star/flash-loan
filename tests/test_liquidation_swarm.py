import unittest
from decimal import Decimal
from unittest.mock import patch

from zero.liquidation_swarm import scan_liquidation_watchlist
from zero.liquidations import LiquidationState, ReservePosition
from zero.swarm import ScanContext, SwarmCandidate, SwarmSupervisor
from zero.venues.base import PoolRef, VenueQuote

USDC = '0x' + '11' * 20
WETH = '0x' + '22' * 20
USER1 = '0x' + '33' * 20
USER2 = '0x' + '44' * 20
POOL = '0x' + '55' * 20


def make_state(user):
    return LiquidationState(
        777, user, '0x' + '66' * 20, '0x' + '77' * 20,
        Decimal('0.90'), Decimal('4000'), Decimal('1000'), (
            ReservePosition(WETH, 18, Decimal('2000'), 2 * 10**18, 0, True,
                            8000, 10500, 1000),
            ReservePosition(USDC, 6, Decimal('1'), 0, 1_000_000_000, False,
                            0, 0, 0),
        ))


class Adapter:
    exact_quote_supported = True
    execution_supported = True
    def discover_pair(self, token_a, token_b, block):
        first, second = sorted((token_a, token_b), key=lambda x: int(x, 16))
        return [PoolRef('uniswap_v3', POOL, first, second, 500, 'uniswap_v3')]
    def quote_exact_input(self, pool, token_in, amount_in, block):
        return VenueQuote(1_045_000_000, 80_000, 500)


class FakeEngine:
    class aave:
        @staticmethod
        def pool_address(block=None):
            return POOL

    class rpc:
        @staticmethod
        def batch_eth_call_results(*args, **kwargs):
            raise RuntimeError('no batch in fixture')


HOT_USER = '0x' + '66' * 20


def _account_data_words(words):
    out = b''
    for value in words:
        out += value.to_bytes(32, 'big')
    return out


class PreFilterRpc:
    def __init__(self, results):
        self.results = results
        self.batched = 0

    def batch_eth_call_results(self, calls, block='latest', max_batch=100):
        self.batched += 1
        return [self.results[call[1]] for call in calls]


class PreFilterEngine:
    def __init__(self, results):
        self.aave = type('Aave', (), {'pool_address':
                         staticmethod(lambda block=None: POOL)})()
        self.rpc = PreFilterRpc(results)


class TestWatchlistPreFilter(unittest.TestCase):
    def context(self):
        return ScanContext(777, 'pool', 'oracle', 5, 2000.0, 0.20, {}, {})

    def test_only_hot_band_borrowers_get_full_state_scan(self):
        cold = '0x' + 'aa' * 20
        hot = HOT_USER
        calldata_by_borrower = {}
        from zero.keccak import selector_hex
        from zero.rpc import encode_address
        selector = selector_hex('getUserAccountData(address)')
        for hf, borrower in ((2.0 * 10**18, cold), (1.05 * 10**18, hot)):
            calldata = selector + encode_address(borrower)[2:]
            calldata_by_borrower[calldata] = _account_data_words(
                [10**12, 10**12, 0, 1, 0, int(hf)])
        engine = PreFilterEngine(calldata_by_borrower)
        cfg = {'liquidation': {'borrowers': [hot, cold], 'pre_filter_hf': 1.10}}
        with patch('zero.liquidation_swarm.build_liquidation_state',
                   return_value=make_state(hot)) as state_builder:
            candidates, errors = scan_liquidation_watchlist(
                engine, cfg, self.context(), {'uniswap_v3': Adapter()},
                model_reserve_usd=0.30)
        self.assertEqual(errors, [])
        scanned = {call.args[1].lower() for call in
                   state_builder.call_args_list}
        self.assertEqual(scanned, {hot.lower()})
        self.assertEqual(len(candidates), 1)

    def test_default_full_scan_boundary_skips_non_liquidatable_nearby_account(self):
        nearby = HOT_USER
        from zero.keccak import selector_hex
        from zero.rpc import encode_address
        calldata = selector_hex('getUserAccountData(address)') + encode_address(nearby)[2:]
        engine = PreFilterEngine({
            calldata: _account_data_words([10**12, 10**12, 0, 1, 0, int(1.05 * 10**18)])
        })
        cfg = {'liquidation': {'borrowers': [nearby]}}
        with patch('zero.liquidation_swarm.build_liquidation_state') as state_builder:
            candidates, errors = scan_liquidation_watchlist(
                engine, cfg, self.context(), {'uniswap_v3': Adapter()},
                model_reserve_usd=0.30)
        self.assertEqual(candidates, [])
        self.assertEqual(errors, [])
        state_builder.assert_not_called()

    def test_pre_filter_failure_scans_everyone(self):
        class BrokenRpc:
            def batch_eth_call_results(self, *args, **kwargs):
                raise RuntimeError('batch down')

        engine = PreFilterEngine({})
        engine.rpc = BrokenRpc()
        cfg = {'liquidation': {'borrowers': [USER1, USER2]}}
        with patch('zero.liquidation_swarm.build_liquidation_state',
                   return_value=make_state(USER2)) as state_builder:
            candidates, errors = scan_liquidation_watchlist(
                engine, cfg, self.context(), {'uniswap_v3': Adapter()},
                model_reserve_usd=0.30)
        self.assertEqual(errors, [])
        self.assertEqual(len(candidates), 2)
        self.assertEqual(state_builder.call_count, 2)


class TestLiquidationWatchlist(unittest.TestCase):
    def context(self):
        return ScanContext(777, 'pool', 'oracle', 5, 2000.0, 0.20, {}, {})

    def test_duplicate_borrower_is_scanned_once_and_candidate_is_rankable(self):
        cfg = {'liquidation': {'borrowers': [USER1, USER1]}}
        with patch('zero.liquidation_swarm.build_liquidation_state',
                   return_value=make_state(USER1)) as state_builder:
            candidates, errors = scan_liquidation_watchlist(
                FakeEngine(), cfg, self.context(), {'uniswap_v3': Adapter()},
                model_reserve_usd=0.30)
        self.assertEqual(errors, [])
        self.assertEqual(state_builder.call_count, 1)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].manager_id, 'LIQUIDATION')
        self.assertAlmostEqual(candidates[0].expected_net, 44.0)
        self.assertEqual(candidates[0].payload['candidate']['strategy'],
                         'swarm_liquidation')
        self.assertTrue(candidates[0].payload['candidate']['executable'])
        self.assertEqual(candidates[0].payload['candidate']['base_decimals'], 6)
        self.assertEqual(candidates[0].payload['candidate']['base_price_usd'], 1.0)

    def test_borrower_failure_is_isolated(self):
        cfg = {'liquidation': {'borrowers': [USER1, USER2]}}
        def build(_aave, borrower, _block):
            if borrower.lower() == USER1.lower():
                raise RuntimeError('bad borrower')
            return make_state(USER2)
        with patch('zero.liquidation_swarm.build_liquidation_state', side_effect=build):
            candidates, errors = scan_liquidation_watchlist(
                FakeEngine(), cfg, self.context(), {'uniswap_v3': Adapter()},
                model_reserve_usd=0.30)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['borrower'], USER1.lower())


class EmptyRpc:
    def block_number(self): return 777


class EmptyEngine:
    rpc = EmptyRpc()


class ExtraSupervisor(SwarmSupervisor):
    def _extra_candidates(self, block, context):
        return [SwarmCandidate(
            candidate_id='liq-id', route_id='liq-route', worker_id='LIQ-1',
            manager_id='LIQUIDATION', block=block, loan_size=1000,
            gross_profit=45, flash_fee=0.5, gas_cost=0.2,
            model_reserve=0.3, expected_net=44, roi=0.044,
            timestamp=0.0, payload={'candidate': {'executable': False}})]


class NullLedger:
    def record(self, **kwargs): pass
    def record_cycle(self, **kwargs): pass


class TestCommonRanking(unittest.TestCase):
    def test_extra_liquidation_candidate_shares_opportunity_ranking(self):
        cfg = {'swarm': {
            'work_stealing': True, 'max_rpc_concurrency': 20,
            'verify_positive_candidates': True, 'max_fork_concurrency': 1,
            'manager_count': 1, 'worker_count': 1,
            'managers': [{'id': 'M', 'workers': [
                {'id': 'W', 'pair': ['A', 'B'], 'role': 'test'}]}],
        }, 'arbitrage': {'execution': {'swap_router_02': '0x' + '00' * 20}}}
        arb_row = {
            'block': 777, 'route_id': 'arb-route', 'size': 100,
            'loan_notional_usd': 100, 'gross_usd': 2,
            'flash_fee_usd': 0.1, 'gas_usd': 0.2,
            'model_reserve_usd': 0.1, 'expected_net_usd': 1.6,
            'candidate': {'base_asset': USDC, 'loan_size': 100,
                          'predicted_net': 1.6, 'min_profit': 0,
                          'executable': False},
        }
        def runner(worker, allocator, context):
            return {'rows': [arb_row], 'routes_scanned': 1,
                    'duplicates_suppressed': 0, 'route_errors': []}
        supervisor = ExtraSupervisor(
            EmptyEngine(), cfg, NullLedger(), worker_runner=runner,
            catalog_builder=lambda block, workers: ([], object()))
        result = supervisor.run_block(777)
        self.assertEqual(result['positive_net'], 2)
        self.assertEqual(result['best_expected_net'], 44)
        self.assertEqual(result['candidates'][0]['manager_id'], 'LIQUIDATION')


if __name__ == '__main__':
    unittest.main()
