import unittest

from zero.calldata import (
    ADDRESS_THIS,
    MSG_SENDER,
    SWAP_ROUTER_02,
    build_uniswap_v3_steps,
)
from zero.candidate import ArbitrageCandidate
from zero.keccak import selector_hex


USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"


def candidate():
    return ArbitrageCandidate(
        block=123,
        name="USDC round trip",
        base_asset=USDC,
        quote_asset=WETH,
        base_decimals=6,
        quote_decimals=18,
        loan_size=1000.0,
        hop1_expected_out=0.285714285714,
        hop2_expected_out=1004.0,
        fee1=500,
        fee2=3000,
        flash_premium_bps=5,
        gas_cost_usd=1.0,
        gross_profit=4.0,
        predicted_net=2.5,
        min_profit=2.0,
    )


def words(data: str) -> list[int]:
    raw = bytes.fromhex(data[10:])
    return [int.from_bytes(raw[i:i + 32], "big") for i in range(0, len(raw), 32)]


class TestSwapRouter02Calldata(unittest.TestCase):
    def test_builds_approve_then_two_exact_input_single_steps(self):
        c = candidate()
        steps = build_uniswap_v3_steps(c, SWAP_ROUTER_02, slippage_bps=20)
        self.assertEqual(len(steps), 3)

        approve = steps[0]
        self.assertEqual(approve.target.lower(), USDC.lower())
        self.assertEqual(approve.value, 0)
        self.assertEqual(approve.data[:10], selector_hex("approve(address,uint256)"))
        approve_words = words(approve.data)
        self.assertEqual(approve_words[0], int(SWAP_ROUTER_02, 16))
        self.assertEqual(approve_words[1], c.loan_amount_raw)

        leg1 = steps[1]
        self.assertEqual(leg1.target.lower(), SWAP_ROUTER_02.lower())
        self.assertEqual(
            leg1.data[:10],
            selector_hex("exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))"),
        )
        w1 = words(leg1.data)
        self.assertEqual(w1[0], int(USDC, 16))
        self.assertEqual(w1[1], int(WETH, 16))
        self.assertEqual(w1[2], 500)
        self.assertEqual(w1[3], int(ADDRESS_THIS, 16))
        self.assertEqual(w1[4], c.loan_amount_raw)
        self.assertEqual(w1[5], c.hop1_expected_out_raw * 9980 // 10000)
        self.assertEqual(w1[6], 0)

        leg2 = steps[2]
        w2 = words(leg2.data)
        self.assertEqual(w2[0], int(WETH, 16))
        self.assertEqual(w2[1], int(USDC, 16))
        self.assertEqual(w2[2], 3000)
        self.assertEqual(w2[3], int(MSG_SENDER, 16))
        self.assertEqual(w2[4], 0)
        self.assertEqual(
            w2[5],
            c.loan_amount_raw + c.flash_fee_raw + c.min_profit_raw,
        )
        self.assertEqual(w2[6], 0)

    def test_rejects_invalid_slippage(self):
        with self.assertRaises(ValueError):
            build_uniswap_v3_steps(candidate(), SWAP_ROUTER_02, slippage_bps=10_001)


if __name__ == "__main__":
    unittest.main()
