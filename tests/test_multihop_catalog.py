import json
import os
import tempfile
import unittest
from unittest.mock import patch

from zero.ledger import Ledger
from zero.pnl import PnlSwarmSupervisor
from zero.swarm import TokenInfo


CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'arbitrum.json')
A = '0x' + '11' * 20
B = '0x' + '22' * 20
P1 = '0x' + 'a1' * 20
P2 = '0x' + 'a2' * 20


class FakeRpc:
    def block_number(self):
        return 777


class FakeAave:
    def pool_address(self, block='latest'):
        return '0x' + '01' * 20

    def oracle_address(self, block='latest'):
        return '0x' + '02' * 20


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()
        self.aave = FakeAave()


class TestMultiHopCatalog(unittest.TestCase):
    def test_catalog_adds_bounded_multihop_routes_after_multidex_discovery(self):
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        cfg['swarm']['adaptive_reserve'] = {'enabled': False}
        cfg['swarm']['multihop_enabled'] = True
        cfg['swarm']['multihop_max_routes_per_pair'] = 7
        registry = {
            'USDC': TokenInfo('USDC', A, 6, 1.0),
            'WETH': TokenInfo('WETH', B, 18, 2000.0),
        }
        multidex = [{
            'block': 777, 'route_kind': 'multidex_exact', 'route_id': 'md',
            'base_symbol': 'USDC', 'quote_symbol': 'WETH',
            'base': A, 'quote': B, 'base_decimals': 6, 'quote_decimals': 18,
            'base_price_usd': 1.0, 'quote_price_usd': 2000.0,
            'leg1': {'venue_id': 'uniswap_v3', 'address': P1, 'token0': A,
                     'token1': B, 'fee_tier': 500, 'pool_kind': 'uniswap_v3'},
            'leg2': {'venue_id': 'sushi_v3', 'address': P2, 'token0': A,
                     'token1': B, 'fee_tier': 500, 'pool_kind': 'uniswap_v3'},
        }]
        multihop = [{
            'block': 777, 'route_kind': 'multihop_exact',
            'route_id': 'mh', 'base_symbol': 'USDC', 'quote_symbol': 'WETH',
            'legs': [{}, {}, {}],
        }]
        engine = FakeEngine()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, 'ledger.db'))
            supervisor = PnlSwarmSupervisor(engine, cfg, ledger)
            with patch('zero.pnl.build_token_registry_batched', return_value=registry), \
                 patch('zero.pnl.discover_uniswap_routes_batched', return_value=[]), \
                 patch('zero.pnl.build_venue_registry', return_value={}), \
                 patch('zero.pnl.discover_route_configs', return_value=multidex), \
                 patch('zero.pnl.build_multihop_route_configs', return_value=multihop) as build_mh, \
                 patch('zero.pnl.build_scan_context_batched', return_value=object()):
                routes, _ = supervisor._build_catalog(777, supervisor.workers)
            ledger.close()
        self.assertIn(multidex[0], routes)
        self.assertIn(multihop[0], routes)
        self.assertGreater(build_mh.call_count, 0)
        args, kwargs = build_mh.call_args
        self.assertTrue(args[3])
        self.assertEqual(kwargs['max_routes'], 7)


if __name__ == '__main__':
    unittest.main()
