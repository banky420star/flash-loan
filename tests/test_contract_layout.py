from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "src" / "ZeroForkExecutor.sol"
FORK_TEST = ROOT / "contracts" / "test" / "ZeroForkExecutorFork.t.sol"


class TestForkContractLayout(unittest.TestCase):
    def test_executor_contract_has_flash_loan_callback_and_guards(self):
        source = CONTRACT.read_text()
        self.assertIn("contract ZeroForkExecutor", source)
        self.assertIn("flashLoanSimple", source)
        self.assertIn("function executeOperation", source)
        self.assertIn("msg.sender != pool", source)
        self.assertIn("initiator != address(this)", source)
        self.assertIn("target.call", source)
        self.assertNotIn("tx.origin", source)

    def test_fork_test_uses_real_arbitrum_aave_and_weth(self):
        source = FORK_TEST.read_text()
        self.assertIn("0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb", source)
        self.assertIn("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", source)
        self.assertIn("flash", source.lower())
        self.assertIn("WethFaucet", source)


if __name__ == "__main__":
    unittest.main()
