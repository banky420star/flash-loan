import unittest

from zero.calldata import SWAP_ROUTER_02
from zero.candidate import ArbitrageCandidate
from zero.cli import build_candidate_calldata


USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"


def candidate(name: str, size: float, hop2: float, predicted_net: float,
              min_profit: float) -> dict:
    return ArbitrageCandidate(
        block=777,
        name=name,
        base_asset=USDC,
        quote_asset=WETH,
        base_decimals=6,
        quote_decimals=18,
        loan_size=size,
        hop1_expected_out=size / 3500,
        hop2_expected_out=hop2,
        fee1=500,
        fee2=3000,
        flash_premium_bps=5,
        gas_cost_usd=0.5,
        gross_profit=hop2 - size,
        predicted_net=predicted_net,
        min_profit=min_profit,
    ).as_dict()


CFG = {
    "arbitrage": {
        "execution": {
            "swap_router_02": SWAP_ROUTER_02,
            "slippage_bps": 20,
        }
    }
}


class TestCandidateCli(unittest.TestCase):
    def test_selects_highest_net_candidate_and_builds_three_steps(self):
        result = {
            "arbitrage": {
                "candidates": [
                    candidate("small", 100.0, 103.0, 1.5, 1.0),
                    candidate("best", 1000.0, 1010.0, 7.5, 2.0),
                ]
            }
        }
        payload = build_candidate_calldata(result, CFG)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["candidate"]["name"], "best")
        self.assertEqual(payload["candidate"]["predicted_net"], 7.5)
        self.assertEqual(len(payload["steps"]), 3)
        self.assertEqual(payload["steps"][0]["target"].lower(), USDC.lower())
        self.assertEqual(payload["steps"][1]["target"].lower(), SWAP_ROUTER_02.lower())
        self.assertEqual(payload["steps"][2]["target"].lower(), SWAP_ROUTER_02.lower())

    def test_no_pass_candidates_returns_none(self):
        result = {"arbitrage": {"candidates": []}}
        self.assertIsNone(build_candidate_calldata(result, CFG))


if __name__ == "__main__":
    unittest.main()
