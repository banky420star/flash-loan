import unittest
from unittest.mock import patch

from zero import cli


class TestSwarmVerifierDispatch(unittest.TestCase):
    def test_liquidation_payload_uses_liquidation_fork_runner(self):
        cfg={'rpc_url':'https://rpc'}
        with patch('zero.cli.run_live_liquidation_fork_result',return_value='liq') as liq, \
             patch('zero.cli.run_live_candidate_fork_result',return_value='arb') as arb:
            out=cli._run_swarm_verifier(cfg,{'kind':'liquidation'})
        self.assertEqual(out,'liq')
        liq.assert_called_once()
        arb.assert_not_called()

    def test_default_payload_uses_arbitrage_fork_runner(self):
        cfg={'rpc_url':'https://rpc'}
        with patch('zero.cli.run_live_liquidation_fork_result',return_value='liq') as liq, \
             patch('zero.cli.run_live_candidate_fork_result',return_value='arb') as arb:
            out=cli._run_swarm_verifier(cfg,{'candidate':{}})
        self.assertEqual(out,'arb')
        arb.assert_called_once()
        liq.assert_not_called()


if __name__=='__main__': unittest.main()
