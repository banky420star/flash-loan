from pathlib import Path
import unittest


class TestZeroExecutorForkLayout(unittest.TestCase):
    def test_fork_fixture_uses_allowlists_and_typed_steps(self):
        text=Path('contracts/test/ZeroExecutorFork.t.sol').read_text()
        for marker in [
            'new ZeroExecutor', 'setToken(', 'setRouter(', 'setSelector(',
            'setMaxLoan(', 'StepKind.SwapExactInputSingle',
            'executor.run', 'MIN_PROFIT_NOT_REALIZED']:
            self.assertIn(marker,text)
        self.assertNotIn('ZeroForkExecutor',text)

    def test_script_runs_hardened_executor_fixture(self):
        text=Path('scripts/hardened_executor_fork_test.sh').read_text()
        self.assertIn('ZeroExecutorForkTest',text)
        self.assertIn('--fork-url',text)
        self.assertIn('--fork-block-number',text)


if __name__=='__main__': unittest.main()
