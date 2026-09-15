import unittest

from zero.keccak import selector_hex
from zero.swarm import TokenInfo
from zero.swarm_batch import build_token_registry_batched


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


class FakeEngine:
    def __init__(self):
        self.aave = FakeAave()
        self.rpc = FakeRpc()


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


if __name__ == "__main__":
    unittest.main()
