import unittest

from zero.keccak import selector_hex
from zero.uniswap_quoter import QUOTER_V2_ARBITRUM, UniswapV3Quoter


TOKEN_IN = "0x" + "11" * 20
TOKEN_OUT = "0x" + "22" * 20


class FakeRpc:
    def __init__(self):
        self.calls = []

    def eth_call(self, to, data, block="latest"):
        self.calls.append((to, data, block))
        words = [123456789, 987654321, 7, 54321]
        return b"".join(value.to_bytes(32, "big") for value in words)


class TestUniswapV3Quoter(unittest.TestCase):
    def test_quote_exact_input_single_is_tick_aware_and_block_pinned(self):
        rpc = FakeRpc()
        quoter = UniswapV3Quoter(rpc)

        result = quoter.quote_exact_input_single(
            token_in=TOKEN_IN,
            token_out=TOKEN_OUT,
            fee=3000,
            amount_in=25_000_000,
            block=505570505,
        )

        self.assertEqual(result.amount_out, 123456789)
        self.assertEqual(result.initialized_ticks_crossed, 7)
        self.assertEqual(result.gas_estimate, 54321)
        self.assertEqual(len(rpc.calls), 1)
        to, data, block = rpc.calls[0]
        self.assertEqual(to.lower(), QUOTER_V2_ARBITRUM.lower())
        self.assertEqual(block, 505570505)
        self.assertTrue(data.startswith(selector_hex(
            "quoteExactInputSingle((address,address,uint256,uint24,uint160))"
        )))
        # Static tuple payload: tokenIn, tokenOut, amountIn, fee, sqrtPriceLimitX96.
        payload = bytes.fromhex(data[10:])
        words = [payload[i:i + 32] for i in range(0, len(payload), 32)]
        self.assertEqual(len(words), 5)
        self.assertEqual(int.from_bytes(words[2], "big"), 25_000_000)
        self.assertEqual(int.from_bytes(words[3], "big"), 3000)
        self.assertEqual(int.from_bytes(words[4], "big"), 0)


if __name__ == "__main__":
    unittest.main()
