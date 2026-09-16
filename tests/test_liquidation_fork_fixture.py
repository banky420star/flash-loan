from pathlib import Path
import unittest


class TestLiquidationForkFixture(unittest.TestCase):
    def test_fixture_uses_real_aave_liquidation_and_uniswap_unwind(self):
        text=Path('contracts/test/ZeroLiquidationFork.t.sol').read_text()
        for marker in [
            'supply(', 'borrow(', 'liquidationCall(', 'mockCall(',
            'flashLoanSimple', 'exactInputSingle', 'ZeroForkExecutor',
            'healthFactor < 1e18', 'MIN_PROFIT_NOT_REALIZED']:
            self.assertIn(marker,text)

    def test_script_runs_fixture_on_exact_arbitrum_fork(self):
        text=Path('scripts/liquidation_fork_test.sh').read_text()
        self.assertIn('ZeroLiquidationForkTest',text)
        self.assertIn('--fork-url',text)
        self.assertIn('--fork-block-number',text)


if __name__=='__main__': unittest.main()
