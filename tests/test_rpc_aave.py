import json
import unittest

from zero.aave import AaveV3, bucket_for
from zero.keccak import keccak256, selector_hex


def _id(text: str) -> bytes:
    return text.encode().ljust(32, b"\0")
from zero.rpc import Rpc, encode_address, encode_bytes32, encode_uint


class FakeTransport:
    """Fake JSON-RPC transport: handlers map (method, params-tuple) -> result."""

    def __init__(self, handlers: dict, fail_first: int = 0):
        self.handlers = handlers
        self.fail_first = fail_first

    def post(self, payload: bytes) -> bytes:
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ConnectionError("transient")
        docs = json.loads(payload)
        single = isinstance(docs, dict)
        docs = [docs] if single else docs
        out = []
        for doc in docs:
            key = (doc["method"],
                   json.dumps(doc["params"], sort_keys=True))
            out.append({"jsonrpc": "2.0", "id": doc["id"],
                        "result": self.handlers[key]})
        return json.dumps(out[0] if single else out).encode()


PROVIDER = "0x" + "ee" * 20
POOL = "0x" + "ab" * 20
ORACLE = "0x" + "cd" * 20
USER = "0x" + "11" * 20
ASSET = "0x" + "22" * 20


def call_key(to: str, data: str):
    return ("eth_call", json.dumps([{"to": to, "data": data}, "latest"],
                                   sort_keys=True))


def eth_call_result(words: list) -> str:
    return "0x" + "".join(w.to_bytes(32, "big").hex() for w in words)


def handlers() -> dict:
    return {
        ("eth_chainId", "[]"): "0xa4b1",
        ("eth_blockNumber", "[]"): "0x64",
        call_key(PROVIDER, selector_hex("getAddress(bytes32)")
                 + encode_bytes32(_id("POOL"))[2:]): encode_uint(int(POOL, 16)),
        call_key(PROVIDER, selector_hex("getAddress(bytes32)")
                 + encode_bytes32(_id("PRICE_ORACLE"))[2:]):
            encode_uint(int(ORACLE, 16)),
        call_key(POOL, selector_hex("getUserAccountData(address)")
                 + encode_address(USER)[2:]): eth_call_result([
            50_000 * 10**8,   # total collateral USD
            49_000 * 10**8,   # total debt USD
            0,                # available borrows
            8000,             # liquidation threshold, bps (80%)
            0,                # LTV
            997 * 10**15,     # health factor 0.997
        ]),
        call_key(ORACLE, selector_hex("getAssetPrice(address)")
                 + encode_address(ASSET)[2:]):
            encode_uint(3500 * 10**8),
    }


class TestRpcAndAave(unittest.TestCase):
    def test_retry_on_transient_failure(self):
        rpc = Rpc("http://fake", transport=FakeTransport(handlers(),
                                                         fail_first=2))
        self.assertEqual(rpc.chain_id(), 42161)

    def test_batch_preserves_order(self):
        rpc = Rpc("http://fake", transport=FakeTransport(handlers()))
        results = rpc.batch([("eth_chainId", []), ("eth_blockNumber", [])])
        self.assertEqual(results[0], "0xa4b1")
        self.assertEqual(results[1], "0x64")

    def test_provider_resolution(self):
        rpc = Rpc("http://fake", transport=FakeTransport(handlers()))
        aave = AaveV3(rpc, PROVIDER)
        self.assertEqual(aave.pool_address(), POOL)
        self.assertEqual(aave.oracle_address(), ORACLE)

    def test_account_data_parse(self):
        rpc = Rpc("http://fake", transport=FakeTransport(handlers()))
        aave = AaveV3(rpc, PROVIDER)
        d = aave.account_data(POOL, USER)
        self.assertAlmostEqual(d["collateral_usd"], 50_000.0)
        self.assertAlmostEqual(d["debt_usd"], 49_000.0)
        self.assertAlmostEqual(d["liquidation_threshold"], 0.8)
        self.assertAlmostEqual(d["health_factor"], 0.997)
        self.assertEqual(bucket_for(d["health_factor"]), "LIQUIDATABLE")

    def test_price(self):
        rpc = Rpc("http://fake", transport=FakeTransport(handlers()))
        aave = AaveV3(rpc, PROVIDER)
        self.assertAlmostEqual(aave.asset_price(ORACLE, ASSET), 3500.0)


if __name__ == "__main__":
    unittest.main()