import inspect
import json
import unittest

from zero.aave import AaveV3
from zero.rpc import Rpc
from zero.uniswap_v3 import UniswapV3Pool


class CapturingTransport:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.payloads = []

    def post(self, payload: bytes) -> bytes:
        decoded = json.loads(payload)
        self.payloads.append(decoded)
        if isinstance(decoded, list):
            raise AssertionError("batch not expected in this test")
        result = self.results.pop(0) if self.results else "0x" + "00" * 32
        return json.dumps({"jsonrpc": "2.0", "id": decoded["id"], "result": result}).encode()


class TestRpcBlockTags(unittest.TestCase):
    def test_eth_call_encodes_integer_block_tag(self):
        if "block" not in inspect.signature(Rpc.eth_call).parameters:
            self.fail("Rpc.eth_call must accept an explicit block parameter")
        transport = CapturingTransport()
        rpc = Rpc("http://example", transport=transport)
        rpc.eth_call("0x" + "11" * 20, "0x1234", block=123)
        self.assertEqual(transport.payloads[-1]["params"][1], "0x7b")

    def test_latest_remains_backward_compatible(self):
        transport = CapturingTransport()
        rpc = Rpc("http://example", transport=transport)
        rpc.eth_call("0x" + "11" * 20, "0x1234")
        self.assertEqual(transport.payloads[-1]["params"][1], "latest")

    def test_get_code_accepts_explicit_block(self):
        if "block" not in inspect.signature(Rpc.get_code).parameters:
            self.fail("Rpc.get_code must accept an explicit block parameter")
        transport = CapturingTransport(results=["0x6000"])
        rpc = Rpc("http://example", transport=transport)
        rpc.get_code("0x" + "22" * 20, block=456)
        self.assertEqual(transport.payloads[-1]["params"][1], hex(456))

    def test_uniswap_pool_state_uses_one_pinned_block(self):
        if "block" not in inspect.signature(UniswapV3Pool.fetch_state).parameters:
            self.fail("UniswapV3Pool.fetch_state must accept an explicit block parameter")
        word = lambda n: "0x" + n.to_bytes(32, "big").hex()
        transport = CapturingTransport(results=[word(2**96), word(123456)])
        rpc = Rpc("http://example", transport=transport)
        pool = UniswapV3Pool(
            rpc,
            "0x" + "33" * 20,
            "0x" + "44" * 20,
            "0x" + "55" * 20,
            0.05,
            18,
            6,
        )
        state = pool.fetch_state(block=123)
        self.assertEqual(state["sqrtPriceX96"], 2**96)
        self.assertEqual(state["liquidity"], 123456)
        self.assertEqual([p["params"][1] for p in transport.payloads], ["0x7b", "0x7b"])

    def test_aave_scanner_reads_propagate_pinned_block(self):
        required = [
            "pool_address",
            "oracle_address",
            "asset_price",
            "flashloan_premium_total",
            "reserves_list",
            "symbol",
            "account_data",
        ]
        missing = [name for name in required if "block" not in inspect.signature(getattr(AaveV3, name)).parameters]
        self.assertEqual(missing, [], f"Aave reads missing block parameter: {missing}")


if __name__ == "__main__":
    unittest.main()
