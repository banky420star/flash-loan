import unittest

from zero.liquidations import build_liquidation_state

A = '0x' + '11' * 20
B = '0x' + '22' * 20
USER = '0x' + '33' * 20
POOL = '0x' + '44' * 20
DATA = '0x' + '55' * 20
ORACLE = '0x' + '66' * 20


class FakeAave:
    def __init__(self, hf=0.97, collateral_raw=3_000_000_000,
                 debt_raw=3_000_000_000):
        self.hf = hf
        self.collateral_raw = collateral_raw
        self.debt_raw = debt_raw
        self.blocks = []

    def pool_address(self, block='latest'):
        self.blocks.append(block); return POOL
    def data_provider_address(self, block='latest'):
        self.blocks.append(block); return DATA
    def oracle_address(self, block='latest'):
        self.blocks.append(block); return ORACLE
    def account_data(self, pool, user, block='latest'):
        self.blocks.append(block)
        return {'collateral_usd': self.collateral_raw / 1e6,
                'debt_usd': self.debt_raw / 1e6,
                'liquidation_threshold': 0.8,
                'health_factor': self.hf}
    def reserves_list(self, pool, block='latest'):
        self.blocks.append(block); return [A, B]
    def reserve_configuration(self, provider, asset, block='latest'):
        self.blocks.append(block)
        return {'decimals': 6, 'liquidation_bonus_bps': 10500,
                'liquidation_threshold_bps': 8000,
                'usage_as_collateral_enabled': asset == A,
                'is_active': True, 'is_frozen': False}
    def liquidation_protocol_fee(self, provider, asset, block='latest'):
        self.blocks.append(block); return 1000 if asset == A else 0
    def user_reserve_data(self, provider, asset, user, block='latest'):
        self.blocks.append(block)
        if asset == A:
            return {'a_token_balance_raw': self.collateral_raw,
                    'debt_raw': 0, 'usage_as_collateral_enabled': True}
        return {'a_token_balance_raw': 0,
                'debt_raw': self.debt_raw,
                'usage_as_collateral_enabled': False}
    def asset_price(self, oracle, asset, block='latest'):
        self.blocks.append(block); return 1.0


class TestLiquidationState(unittest.TestCase):
    def test_safe_borrower_is_not_liquidatable(self):
        state = build_liquidation_state(FakeAave(hf=1.01), USER, 777)
        self.assertFalse(state.liquidatable)

    def test_large_position_above_095_uses_50_percent_band(self):
        state = build_liquidation_state(FakeAave(hf=0.97), USER, 777)
        self.assertTrue(state.liquidatable)
        self.assertEqual(state.close_factor_bps(A, B), 5000)
        self.assertEqual(state.max_debt_to_cover_raw(A, B), 1_500_000_000)

    def test_hf_at_or_below_095_allows_full_selected_debt(self):
        state = build_liquidation_state(FakeAave(hf=0.95), USER, 777)
        self.assertEqual(state.close_factor_bps(A, B), 10000)
        self.assertEqual(state.max_debt_to_cover_raw(A, B), 3_000_000_000)

    def test_small_reserve_position_allows_full_close(self):
        state = build_liquidation_state(
            FakeAave(hf=0.97, collateral_raw=1_500_000_000,
                     debt_raw=1_500_000_000), USER, 777)
        self.assertEqual(state.close_factor_bps(A, B), 10000)

    def test_reserve_bonus_protocol_fee_and_block_are_preserved(self):
        aave = FakeAave(hf=0.97)
        state = build_liquidation_state(aave, USER, 888)
        collateral = state.position(A)
        self.assertEqual(collateral.liquidation_bonus_bps, 10500)
        self.assertEqual(collateral.liquidation_protocol_fee_bps, 1000)
        self.assertTrue(all(block == 888 for block in aave.blocks))


if __name__ == '__main__':
    unittest.main()
