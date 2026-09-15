from pathlib import Path
import unittest


class TestCandidateForkLayout(unittest.TestCase):
    def test_real_candidate_fork_uses_aave_and_uniswap_route(self):
        text = Path("contracts/test/ZeroCandidateArbFork.t.sol").read_text()
        lower = text.lower()
        addresses = [
            "0xa97684ead0e402dc232d5a977953df7ecbab3cdb",
            "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
            "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
        ]
        for address in addresses:
            self.assertIn(address, lower)
        for marker in ["fee: 500", "fee: 3000", "exactInputSingle", "executor.run"]:
            self.assertIn(marker, text)

    def test_candidate_fork_runner_targets_candidate_contract(self):
        text = Path("scripts/candidate_fork_test.sh").read_text()
        self.assertIn("ZeroCandidateArbForkTest", text)
        self.assertIn("--fork-url", text)
        self.assertIn("--fork-block-number", text)


if __name__ == "__main__":
    unittest.main()
