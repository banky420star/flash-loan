import unittest

from zero.execution_policy import ExecutionPolicy, ExecutionRequest


class TestExecutionPolicy(unittest.TestCase):
    def policy(self):
        return ExecutionPolicy(
            max_block_lag=2, max_gas_usd=5, max_loan_notional_usd=50_000,
            max_consecutive_reverts=3, max_daily_modeled_loss_usd=20)

    def request(self, **changes):
        row=dict(block=100,candidate_id='cid',loan_notional_usd=10_000,
                 gas_usd=1,expected_net_usd=10,deadline_ts=1_100)
        row.update(changes)
        return ExecutionRequest(**row)

    def test_valid_candidate_is_reserved_and_duplicate_is_rejected(self):
        policy=self.policy(); req=self.request()
        first=policy.authorize(req,chain_head=101,now=1_000)
        second=policy.authorize(req,chain_head=101,now=1_000)
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual(second.reason,'duplicate_candidate')

    def test_stale_expired_nonpositive_gas_and_loan_limits(self):
        cases=[
            (self.request(block=98),'stale_block',101,1_000),
            (self.request(deadline_ts=999),'deadline_expired',100,1_000),
            (self.request(expected_net_usd=0),'non_positive_net',100,1_000),
            (self.request(gas_usd=5.01),'gas_limit_exceeded',100,1_000),
            (self.request(loan_notional_usd=50_001),'loan_limit_exceeded',100,1_000),
        ]
        for req,reason,head,now in cases:
            with self.subTest(reason=reason):
                result=self.policy().authorize(req,chain_head=head,now=now)
                self.assertFalse(result.allowed); self.assertEqual(result.reason,reason)

    def test_consecutive_reverts_trip_policy_until_success_resets(self):
        policy=self.policy()
        for i in range(3): policy.record_outcome(False,modeled_loss_usd=1,now=1_000+i)
        denied=policy.authorize(self.request(candidate_id='after'),chain_head=100,now=1_010)
        self.assertFalse(denied.allowed); self.assertEqual(denied.reason,'revert_limit_reached')
        policy.record_outcome(True,modeled_loss_usd=0,now=1_011)
        self.assertTrue(policy.authorize(
            self.request(candidate_id='reset'),chain_head=100,now=1_012).allowed)

    def test_daily_loss_limit_resets_on_next_utc_day(self):
        policy=self.policy()
        policy.record_outcome(False,modeled_loss_usd=21,now=10)
        denied=policy.authorize(self.request(candidate_id='loss'),chain_head=100,now=20)
        self.assertEqual(denied.reason,'daily_loss_limit_reached')
        next_day=86_410
        self.assertTrue(policy.authorize(
            self.request(candidate_id='tomorrow',deadline_ts=90_000),
            chain_head=100,now=next_day).allowed)

    def test_emergency_kill_blocks_everything(self):
        policy=self.policy(); policy.set_kill(True,'manual')
        denied=policy.authorize(self.request(),chain_head=100,now=1_000)
        self.assertFalse(denied.allowed); self.assertEqual(denied.reason,'kill_state')
        self.assertEqual(policy.kill_reason,'manual')


if __name__=='__main__': unittest.main()
