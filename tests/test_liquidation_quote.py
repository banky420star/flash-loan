import unittest
from decimal import Decimal

from zero.liquidations import LiquidationState, ReservePosition
from zero.liquidation_quote import UnwindRoute, quote_liquidation
from zero.routes import RouteLeg
from zero.venues.base import PoolRef, VenueQuote

USDC = '0x' + '11' * 20
WETH = '0x' + '22' * 20
POOL = '0x' + '33' * 20
BORROWER = '0x' + '44' * 20


class Adapter:
    exact_quote_supported = True
    execution_supported = True
    def __init__(self, amount_out):
        self.amount_out = amount_out
        self.calls = []
    def quote_exact_input(self, pool, token_in, amount_in, block):
        self.calls.append((pool.address, token_in, amount_in, block))
        return VenueQuote(self.amount_out, gas_estimate=80_000, fee_used=500)


def state():
    collateral = ReservePosition(
        WETH, 18, Decimal('2000'), 2 * 10**18, 0, True,
        8000, 10500, 1000)
    debt = ReservePosition(
        USDC, 6, Decimal('1'), 0, 1_000_000_000, False,
        0, 0, 0)
    return LiquidationState(
        777, BORROWER, '0x' + '55' * 20, '0x' + '66' * 20,
        Decimal('0.90'), Decimal('4000'), Decimal('1000'),
        (collateral, debt))


def route():
    first, second = sorted((WETH, USDC), key=lambda x: int(x, 16))
    pool = PoolRef('uniswap_v3', POOL, first, second, 500, 'uniswap_v3')
    return UnwindRoute((RouteLeg(pool, WETH, USDC),))


class TestLiquidationQuote(unittest.TestCase):
    def test_bonus_protocol_fee_unwind_and_all_costs_are_applied(self):
        adapter = Adapter(1_045_000_000)
        candidate = quote_liquidation(
            state(), USDC, WETH, route(), 777,
            {'uniswap_v3': adapter}, flash_premium_bps=5,
            gas_usd=Decimal('0.20'), reserve_usd=Decimal('0.30'))
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.debt_to_cover_raw, 1_000_000_000)
        self.assertEqual(candidate.collateral_received_raw, 522_500_000_000_000_000)
        self.assertEqual(candidate.flash_fee_raw, 500_000)
        self.assertEqual(candidate.min_profit_raw, 500_001)
        self.assertEqual(candidate.unwind_out_raw, 1_045_000_000)
        self.assertEqual(candidate.gross_usd, Decimal('45.000000'))
        self.assertEqual(candidate.flash_fee_usd, Decimal('0.500000'))
        self.assertEqual(candidate.expected_net_usd, Decimal('44.000000'))
        self.assertEqual(adapter.calls[0][2], 522_500_000_000_000_000)
        self.assertTrue(candidate.executable)

    def test_non_positive_candidate_is_rejected(self):
        candidate = quote_liquidation(
            state(), USDC, WETH, route(), 777,
            {'uniswap_v3': Adapter(1_000_500_000)}, flash_premium_bps=5,
            gas_usd=Decimal('0.20'), reserve_usd=Decimal('0.30'))
        self.assertIsNone(candidate)

    def test_unwind_must_be_execution_supported(self):
        adapter = Adapter(1_045_000_000)
        adapter.execution_supported = False
        self.assertIsNone(quote_liquidation(
            state(), USDC, WETH, route(), 777,
            {'uniswap_v3': adapter}, flash_premium_bps=5,
            gas_usd=Decimal('0.20'), reserve_usd=Decimal('0.30')))


if __name__ == '__main__':
    unittest.main()
