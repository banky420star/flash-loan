import unittest
from unittest.mock import patch

from zero.calldata import SWAP_ROUTER_02
from zero.pnl import PnlSwarmSupervisor
from zero.swarm import SwarmCandidate

DEBT='0x'+'11'*20
COLL='0x'+'22'*20
BORROWER='0x'+'33'*20
AAVE='0x'+'44'*20
POOL='0x'+'55'*20


class Aave:
    def pool_address(self, block='latest'):
        self.block=block
        return AAVE


class Engine:
    aave=Aave()


class TestLiquidationForkPayload(unittest.TestCase):
    def test_pnl_builds_four_step_liquidation_payload_and_verification(self):
        raw={
            'strategy':'swarm_liquidation','route_kind':'liquidation_exact',
            'executable':True,'block':777,'borrower':BORROWER,
            'base_asset':DEBT,'base_decimals':6,'base_price_usd':1.0,
            'collateral_asset':COLL,'loan_size':1000.0,
            'debt_to_cover_raw':1_000_000_000,
            'collateral_received_raw':522_500_000_000_000_000,
            'unwind_out_raw':1_045_000_000,'flash_fee_raw':500_000,
            'min_profit_raw':500_001,'min_profit':.500001,
            'predicted_net':44.0,'gas_cost_usd':.2,
            'legs':[{'pool':{'venue_id':'uniswap_v3','address':POOL,
                'token0':DEBT,'token1':COLL,'fee_tier':500,
                'pool_kind':'uniswap_v3'},'token_in':COLL,'token_out':DEBT}],
        }
        candidate=SwarmCandidate(
            candidate_id='cid',route_id='rid',worker_id='LIQ-1',
            manager_id='LIQUIDATION',block=777,loan_size=1000,
            gross_profit=45,flash_fee=.5,gas_cost=.2,model_reserve=.3,
            expected_net=44,roi=.044,timestamp=0,
            payload={'candidate':raw,'route':{'base_price_usd':1.0}})
        supervisor=object.__new__(PnlSwarmSupervisor)
        supervisor.engine=Engine()
        supervisor.config={
            'gas_limit':2_000_000,
            'arbitrage':{'execution':{
                'swap_router_02':SWAP_ROUTER_02,'slippage_bps':20}}}
        payload=supervisor._fork_payload(candidate)
        self.assertEqual(payload['kind'],'liquidation')
        self.assertEqual(len(payload['steps']),4)
        self.assertEqual(payload['verification']['strategy'],'swarm_liquidation')
        self.assertEqual(payload['verification']['base_price_usd'],1.0)
        self.assertEqual(supervisor.engine.aave.block,777)


if __name__=='__main__': unittest.main()
