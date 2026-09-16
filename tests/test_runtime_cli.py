import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zero import cli


class Rpc:
    current_endpoint='https://second'
    def block_number(self): return 105


class Engine:
    rpc=Rpc()


class Supervisor:
    engine=Engine()
    def run_block(self):
        return {'block':100,'active_workers':20,'routes_scanned':10,
                'worker_failures':0,'positive_net':0,
                'best_expected_net':None,
                'fork_verifications_attempted':0,
                'fork_verifications_passed':0,
                'fork_verifications_failed':0,'elapsed_s':1.0}


class TestRuntimeCli(unittest.TestCase):
    def test_swarm_once_writes_runtime_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'status.json')
            args=SimpleNamespace(ledger=None)
            with patch('zero.cli._swarm_supervisor',return_value=Supervisor()), \
                 patch.dict(os.environ,{'ZERO_RUNTIME_STATUS_PATH':path}):
                rc=cli.cmd_swarm_once(args)
            with open(path) as handle: raw=json.load(handle)
        self.assertEqual(rc,0)
        self.assertEqual(raw['block'],100)
        self.assertEqual(raw['chain_head'],105)
        self.assertEqual(raw['block_lag'],5)
        self.assertEqual(raw['rpc_endpoint'],'https://second')

    def test_runtime_monitor_path_uses_config_when_env_absent(self):
        with patch.dict(os.environ,{'ZERO_RUNTIME_STATUS_PATH':''}):
            monitor=cli._runtime_monitor({'runtime':{'status_path':'run/custom.json'}})
        self.assertEqual(str(monitor.path),'run/custom.json')


if __name__=='__main__': unittest.main()
