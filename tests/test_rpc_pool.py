import unittest

from zero.rpc import RpcError
from zero.rpc_pool import RpcPool


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes=list(outcomes)
        self.calls=[]
    def eth_call(self, to, data, block='latest'):
        self.calls.append(('eth_call',to,data,block))
        outcome=self.outcomes.pop(0)
        if isinstance(outcome,Exception): raise outcome
        return outcome
    def block_number(self):
        self.calls.append(('block_number',))
        outcome=self.outcomes.pop(0)
        if isinstance(outcome,Exception): raise outcome
        return outcome


class TestRpcPool(unittest.TestCase):
    def test_transport_failure_fails_over_preserving_pinned_block(self):
        now=[0.0]
        primary=FakeClient([RpcError('rpc unreachable after 3 attempts: HTTP Error 429'), b'p'])
        secondary=FakeClient([b's',b's2'])
        clients={'p':primary,'s':secondary}
        pool=RpcPool(['p','s'],client_factory=lambda url:clients[url],
                     cooldown_s=10,clock=lambda:now[0])
        self.assertEqual(pool.eth_call('0x1','0x2',block=777),b's')
        self.assertEqual(primary.calls[0][-1],777)
        self.assertEqual(secondary.calls[0][-1],777)
        self.assertEqual(pool.current_endpoint,'s')
        now[0]=1
        self.assertEqual(pool.eth_call('0x1','0x2',block=778),b's2')
        self.assertEqual(len(primary.calls),1)
        now[0]=11
        self.assertEqual(pool.eth_call('0x1','0x2',block=779),b'p')
        self.assertEqual(pool.current_endpoint,'p')

    def test_json_rpc_application_error_does_not_fail_over(self):
        primary=FakeClient([RpcError("RPC error: {'code': 3, 'message': 'execution reverted'}")])
        secondary=FakeClient([b'wrong'])
        pool=RpcPool(['p','s'],client_factory=lambda url:{'p':primary,'s':secondary}[url])
        with self.assertRaises(RpcError):
            pool.eth_call('0x1','0x2',block=777)
        self.assertEqual(secondary.calls,[])

    def test_endpoint_recovers_after_cooldown(self):
        now=[0.0]
        primary=FakeClient([RpcError('rpc unreachable: timeout'),123])
        secondary=FakeClient([456])
        pool=RpcPool(['p','s'],client_factory=lambda url:{'p':primary,'s':secondary}[url],
                     cooldown_s=5,clock=lambda:now[0])
        self.assertEqual(pool.block_number(),456)
        self.assertGreater(pool.health()['p']['failures'],0)
        now[0]=6
        self.assertEqual(pool.block_number(),123)
        self.assertEqual(pool.health()['p']['failures'],0)

    def test_requires_at_least_one_unique_endpoint(self):
        with self.assertRaises(ValueError): RpcPool([])
        pool=RpcPool(['p','p'],client_factory=lambda url:FakeClient([1]))
        self.assertEqual(pool.endpoints,('p',))


if __name__=='__main__': unittest.main()

class TestRpcPoolEngineWiring(unittest.TestCase):
    def test_shadow_engine_uses_pool_when_rpc_urls_are_configured(self):
        from zero.engine import ShadowEngine
        config={
            'rpc_urls':['https://one','https://two'],
            'aave_provider':'0x'+'11'*20,
            'venues':{}, 'gas_limit':2_000_000, 'gas_price_gwei':0.1,
        }
        engine=ShadowEngine('https://one',config,None)
        self.assertIsInstance(engine.rpc,RpcPool)
        self.assertEqual(engine.rpc.endpoints,('https://one','https://two'))

    def test_shadow_engine_keeps_single_rpc_backward_compatible(self):
        from zero.engine import ShadowEngine
        from zero.rpc import Rpc
        config={'aave_provider':'0x'+'11'*20,'venues':{}}
        engine=ShadowEngine('https://one',config,None)
        self.assertIsInstance(engine.rpc,Rpc)
