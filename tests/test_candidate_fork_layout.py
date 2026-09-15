from pathlib import Path
import unittest


class TestCandidateForkLayout(unittest.TestCase):
    def test_real_candidate_fork_uses_aave_and_uniswap_route(self):
        text = Path("contracts/test/ZeroCandidateArbFork.t.sol").read_text()
        required = [
            "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb",
            "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
            "fee: 500",
            "fee: 3000",
            "exactInputSingle",
            "executor.run",
        ]
        for marker in required:
            self.assertIn(marker, text)

    def test_candidate_fork_runner_targets_candidate_contract(self):
        text = Path("scripts/candidate_fork_test.sh").read_text()
        self.assertIn("ZeroCandidateArbForkTest", text)
        self.assertIn("--fork-url", text)
        self.assertIn("--fork-block-number", text)


if __name__ == "__main__":
    unittest.main()
