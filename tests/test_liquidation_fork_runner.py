import json
import os
import tempfile
import unittest
from unittest.mock import patch

from zero.fork_cli import run_live_liquidation_fork_result

ASSET='0x'+'11'*20


def payload():
    return {
        'kind':'liquidation',
        'candidate':{
            'block':777, 'base_asset':ASSET, 'base_decimals':6,
            'debt_to_cover_raw':1_000_000_000, 'min_profit_raw':500_001,
            'predicted_net':44.0, 'gas_cost_usd':0.2,
        },
        'steps':[{'target':'0x'+f'{i+2:02x}'*20,'value':0,'data':'0x1234'}
                 for i in range(4)],
        'verification':{
            'candidate_id':'liq-cid','route_id':'liq-rid',
            'strategy':'swarm_liquidation','base_price_usd':1.0,
            'model_reserve_usd':0.3,'gas_limit':2_000_000,
        },
    }


class Completed:
    def __init__(self, rc=0, stdout='ok', stderr=''):
        self.returncode=rc; self.stdout=stdout; self.stderr=stderr


class TestLiquidationForkRunner(unittest.TestCase):
    def test_four_step_payload_is_passed_to_dedicated_foundry_runner(self):
        def run(args, env=None, capture_output=None, text=None):
            self.assertEqual(args, ['bash','scripts/live_liquidation_fork_test.sh'])
            self.assertEqual(env['FORK_BLOCK'],'777')
            self.assertEqual(env['ZERO_ASSET'].lower(),ASSET.lower())
            self.assertEqual(env['ZERO_LOAN_RAW'],'1000000000')
            self.assertEqual(env['ZERO_STEP3_DATA'],'0x1234')
            with open(env['ZERO_RESULT_PATH'],'w') as f:
                json.dump({'realized_raw':'45000000','gas_used':'1000000'},f)
            return Completed()
        with tempfile.TemporaryDirectory() as tmp, \
             patch('zero.fork_cli.RESULT_DIR',tmp), \
             patch('zero.fork_cli.subprocess.run',side_effect=run):
            result=run_live_liquidation_fork_result('https://upstream',payload())
        self.assertTrue(result.success)
        self.assertEqual(result.strategy,'swarm_liquidation')
        self.assertAlmostEqual(result.realized_net,44.9)

    def test_requires_exactly_four_steps(self):
        row=payload(); row['steps']=row['steps'][:3]
        with self.assertRaises(ValueError):
            run_live_liquidation_fork_result('https://upstream',row)


if __name__=='__main__': unittest.main()
