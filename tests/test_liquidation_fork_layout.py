from pathlib import Path
import unittest


class TestLiquidationForkLayout(unittest.TestCase):
    def test_live_harness_reads_four_steps_and_writes_result(self):
        text=Path('contracts/test/ZeroLiveLiquidationFork.t.sol').read_text()
        for marker in [
            'envAddress("ZERO_ASSET")', 'envUint("ZERO_LOAN_RAW")',
            'envUint("ZERO_MIN_PROFIT_RAW")', 'envBytes("ZERO_STEP3_DATA")',
            'new ZeroForkExecutor.Step[](4)', 'executor.run', 'writeFile']:
            self.assertIn(marker,text)

    def test_runner_is_exact_block_and_uses_liquidation_contract(self):
        text=Path('scripts/live_liquidation_fork_test.sh').read_text()
        self.assertIn('ZeroLiveLiquidationForkTest',text)
        self.assertIn('--fork-block-number',text)
        self.assertIn('ZERO_STEP3_DATA',text)
        self.assertIn('ZERO_RESULT_PATH',text)


if __name__=='__main__': unittest.main()
