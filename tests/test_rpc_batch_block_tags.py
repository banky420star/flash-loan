import json
import unittest
from unittest.mock import patch

from zero.rpc import Rpc, RpcError


class RecordingTransport:
    def __init__(self, *, fail=False):
        self.payloads = []
        self.fail = fail

    def post(self, payload: bytes) -> bytes:
        decoded = json.loads(payload)
        self.payloads.append(decoded)
        if self.fail:
            return json.dumps([
                {"jsonrpc": "2.0", "id": call["id"],
                 "error": {"code": -32000, "message": "boom"}}
                for call in decoded
            ]).encode()
        return json.dumps([
            {"jsonrpc": "2.0", "id": call["id"],
             "result": "0x" + call["params"][0]["data"].removeprefix("0x").rjust(64, "0")[-64:]}
            for call in decoded
        ]).encode()


class TransientTransport(RecordingTransport):
    def __init__(self, failures=1):
        super().__init__()
        self.failures = failures
        self.attempts = 0

    def post(self, payload: bytes) -> bytes:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError("temporary throttle")
        return super().post(payload)


class TestPinnedBatchEthCall(unittest.TestCase):
    def test_all_calls_use_same_integer_block_tag_and_preserve_order(self):
        transport = RecordingTransport()
        rpc = Rpc("http://example", transport=transport)
        out = rpc.batch_eth_call([
            ("0x" + "11" * 20, "0x01"),
            ("0x" + "22" * 20, "0x02"),
            ("0x" + "33" * 20, "0x03"),
        ], block=123, max_batch=100)

        self.assertEqual(len(transport.payloads), 1)
        payload = transport.payloads[0]
        self.assertEqual([call["params"][1] for call in payload], ["0x7b"] * 3)
        self.assertEqual([call["params"][0]["data"] for call in payload],
                         ["0x01", "0x02", "0x03"])
        self.assertEqual([int.from_bytes(value, "big") for value in out], [1, 2, 3])

    def test_calls_are_chunked_at_max_batch_without_reordering(self):
        transport = RecordingTransport()
        rpc = Rpc("http://example", transport=transport)
        calls = [("0x" + "11" * 20, f"0x{i:02x}") for i in range(1, 6)]
        out = rpc.batch_eth_call(calls, block=456, max_batch=2)

        self.assertEqual([len(payload) for payload in transport.payloads], [2, 2, 1])
        self.assertEqual([int.from_bytes(value, "big") for value in out],
                         [1, 2, 3, 4, 5])
        for payload in transport.payloads:
            self.assertTrue(all(call["params"][1] == hex(456) for call in payload))

    def test_batch_retries_transient_transport_failure(self):
        transport = TransientTransport(failures=1)
        rpc = Rpc("http://example", transport=transport, retries=3)
        with patch("zero.rpc.time.sleep") as sleep:
            out = rpc.batch_eth_call([
                ("0x" + "11" * 20, "0x01"),
            ], block=789, max_batch=10)
        self.assertEqual(transport.attempts, 2)
        sleep.assert_called_once()
        self.assertEqual(int.from_bytes(out[0], "big"), 1)

    def test_invalid_batch_size_is_rejected(self):
        rpc = Rpc("http://example", transport=RecordingTransport())
        with self.assertRaises(ValueError):
            rpc.batch_eth_call([], block=1, max_batch=0)

    def test_rpc_batch_error_raises_and_never_falls_back(self):
        transport = RecordingTransport(fail=True)
        rpc = Rpc("http://example", transport=transport)
        with self.assertRaises(RpcError):
            rpc.batch_eth_call([
                ("0x" + "11" * 20, "0x01"),
            ], block=999, max_batch=10)
        self.assertEqual(len(transport.payloads), 1)
        self.assertEqual(transport.payloads[0][0]["params"][1], hex(999))


if __name__ == "__main__":
    unittest.main()
