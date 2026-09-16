import unittest
from unittest.mock import patch

from zero.pnl import PnlSwarmSupervisor
from zero.swarm import SwarmCandidate


class Ledger:
    def __init__(self): self.rows=[]
    def record(self, **kwargs): self.rows.append(kwargs)


class Engine:
    venues={'uniswap_v3': object()}


class TestPnlLiquidationHook(unittest.TestCase):
    def test_extra_candidates_scan_and_persist_liquidation_rows(self):
        candidate = SwarmCandidate(
            candidate_id='cid', route_id='rid', worker_id='LIQ-1',
            manager_id='LIQUIDATION', block=777, loan_size=1000,
            gross_profit=45, flash_fee=0.5, gas_cost=0.2,
            model_reserve=0.3, expected_net=44, roi=.044,
            timestamp=0.0, payload={'candidate': {
                'strategy':'swarm_liquidation', 'borrower':'0xabc',
                'base_asset':'0xdebt', 'min_profit':.5, 'executable':False}})
        ledger=Ledger()
        supervisor=object.__new__(PnlSwarmSupervisor)
        supervisor.engine=Engine()
        supervisor.config={'liquidation': {'borrowers':['0xabc']}}
        supervisor.ledger=ledger
        supervisor._cycle_model_reserve_usd=.3
        with patch('zero.pnl.scan_liquidation_watchlist',
                   return_value=([candidate],[{'block':777,'borrower':'0xbad','error':'boom'}])) as scan:
            out=supervisor._extra_candidates(777, object())
        self.assertEqual(out,[candidate])
        self.assertEqual(scan.call_count,1)
        self.assertEqual(ledger.rows[0]['strategy'],'swarm_liquidation')
        self.assertEqual(ledger.rows[1]['strategy'],'swarm_liquidation_worker')


if __name__=='__main__': unittest.main()
