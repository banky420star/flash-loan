import json
import unittest

from zero.keccak import selector_hex
from zero.rpc import Rpc
from zero.swarm import TokenInfo
from zero.swarm_batch import (
    build_token_registry_batched, refresh_token_prices_batched,
)


TOKEN_A = "0x" + "11" * 20
TOKEN_B = "0x" + "22" * 20
POOL = "0x" + "33" * 20
ORACLE = "0x" + "44" * 20


def abi_uint(value: int) -> bytes:
    return value.to_bytes(32, "big")


def abi_string(value: str) -> bytes:
    raw = value.encode()
    padded = raw + b"\0" * ((32 - len(raw) % 32) % 32)
    return abi_uint(32) + abi_uint(len(raw)) + padded


def rpc_hex(raw: bytes) -> str:
    return "0x" + raw.hex()


class FakeAave:
    def __init__(self):
        self.calls = []

    def reserves_list(self, pool, block="latest"):
        self.calls.append((pool, block))
        return [TOKEN_A, TOKEN_B]


class FakeRpc:
    def __init__(self):
        self.batches = []

    def batch_eth_call(self, calls, *, block="latest", max_batch=100):
        self.batches.append((list(calls), block, max_batch))
        out = []
        for to, data in calls:
            selector = data[:10]
            if to.lower() == TOKEN_A.lower():
                if selector == selector_hex("symbol()"):
                    out.append(abi_string("TOKA"))
                elif selector == selector_hex("decimals()"):
                    out.append(abi_uint(18))
                else:
                    raise AssertionError("unexpected token A call")
            elif to.lower() == TOKEN_B.lower():
                if selector == selector_hex("symbol()"):
                    out.append(b"")  # malformed row must be skipped
                elif selector == selector_hex("decimals()"):
                    out.append(abi_uint(6))
                else:
                    raise AssertionError("unexpected token B call")
            elif to.lower() == ORACLE.lower():
                # Aave oracle price, 8 decimals.
                if selector != selector_hex("getAssetPrice(address)"):
                    raise AssertionError("unexpected oracle call")
                out.append(abi_uint(2500 * 10**8 if TOKEN_A[2:] in data else 1 * 10**8))
            else:
                raise AssertionError(f"unexpected target {to}")
        return out


class MemberErrorTransport:
    """Return one JSON-RPC member error while every other member succeeds."""

    def __init__(self):
        self.payloads = []

    def post(self, payload: bytes) -> bytes:
        calls = json.loads(payload)
        self.payloads.append(calls)
        results = []
        for call in calls:
            idx = call["id"]
            if idx == 3:  # TOKEN_B symbol()
                results.append({
                    "jsonrpc": "2.0", "id": idx,
                    "error": {"code": -32000, "message": "token symbol reverted"},
                })
                continue
            values = {
                0: abi_string("TOKA"),
                1: abi_uint(18),
                2: abi_uint(2500 * 10**8),
                4: abi_uint(6),
                5: abi_uint(1 * 10**8),
            }
            results.append({
                "jsonrpc": "2.0", "id": idx,
                "result": rpc_hex(values[idx]),
            })
        return json.dumps(results).encode()


class FakeEngine:
    def __init__(self, rpc=None):
        self.aave = FakeAave()
        self.rpc = rpc or FakeRpc()


class TestBatchedTokenRegistry(unittest.TestCase):
    def test_metadata_and_prices_use_one_pinned_batch_and_skip_bad_row(self):
        engine = FakeEngine()
        registry = build_token_registry_batched(
            engine, 123, pool=POOL, oracle=ORACLE, max_batch=100)

        self.assertEqual(engine.aave.calls, [(POOL, 123)])
        self.assertEqual(len(engine.rpc.batches), 1)
        calls, block, max_batch = engine.rpc.batches[0]
        self.assertEqual(block, 123)
        self.assertEqual(max_batch, 100)
        self.assertEqual(len(calls), 6)
        self.assertIn("TOKA", registry)
        self.assertNotIn("TOKB", registry)
        self.assertEqual(registry["TOKA"], TokenInfo(
            symbol="TOKA", address=TOKEN_A.lower(), decimals=18,
            price_usd=2500.0))

    def test_price_refresh_preserves_static_metadata_and_uses_one_pinned_batch(self):
        engine = FakeEngine()
        static = {
            "TOKA": TokenInfo("TOKA", TOKEN_A.lower(), 18, 0.0),
            "TOKB": TokenInfo("TOKB", TOKEN_B.lower(), 6, 0.0),
        }
        refreshed = refresh_token_prices_batched(
            engine, 124, static, oracle=ORACLE, max_batch=100)

        self.assertEqual(len(engine.rpc.batches), 1)
        calls, block, max_batch = engine.rpc.batches[0]
        self.assertEqual(block, 124)
        self.assertEqual(max_batch, 100)
        self.assertEqual(len(calls), 2)
        self.assertEqual(refreshed["TOKA"], TokenInfo(
            "TOKA", TOKEN_A.lower(), 18, 2500.0))
        self.assertEqual(refreshed["TOKB"], TokenInfo(
            "TOKB", TOKEN_B.lower(), 6, 1.0))
        self.assertEqual(engine.aave.calls, [])

    def test_one_rpc_member_error_skips_only_that_token(self):
        transport = MemberErrorTransport()
        engine = FakeEngine(Rpc("http://example", transport=transport, retries=1))

        registry = build_token_registry_batched(
            engine, 123, pool=POOL, oracle=ORACLE, max_batch=100)

        self.assertEqual(len(transport.payloads), 1)
        self.assertTrue(all(call["params"][1] == hex(123)
                            for call in transport.payloads[0]))
        self.assertEqual(registry, {
            "TOKA": TokenInfo(
                symbol="TOKA", address=TOKEN_A.lower(), decimals=18,
                price_usd=2500.0),
        })


if __name__ == "__main__":
    unittest.main()
