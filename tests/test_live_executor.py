import time
import unittest
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zero import live_executor
from zero.live_executor import (
    GateDenied,
    build_run_calldata,
    check_gate,
    encode_steps,
    record_gas_spend,
)

CFG = {"max_notional_usd": 500, "min_net_usd": 5.0,
       "daily_gas_cap_usd": 10.0}


class TestGate(unittest.TestCase):
    def setUp(self):
        # isolate the gas log so tests never read/write run/live_gas_log.json
        import tempfile
        self._saved = live_executor.GAS_LOG_PATH
        live_executor.GAS_LOG_PATH = (
            Path(tempfile.mkdtemp()) / "gas_log.json")
        self.addCleanup(setattr, live_executor, "GAS_LOG_PATH", self._saved)

    def test_gate_passes_on_good_candidate(self):
        report = check_gate({"expected_net_usd": 6.0, "notional_usd": 300},
                            CFG, now=1_000_000)
        self.assertEqual(report["net_usd"], 6.0)

    def test_gate_denies_low_net(self):
        with self.assertRaises(GateDenied):
            check_gate({"expected_net_usd": 0.05, "notional_usd": 25},
                       CFG, now=1_000_000)

    def test_gate_denies_oversize_notional(self):
        with self.assertRaises(GateDenied):
            check_gate({"expected_net_usd": 50.0, "notional_usd": 900},
                       CFG, now=1_000_000)

    def test_daily_gas_cap_blocks_after_losses(self):
        record_gas_spend(4.0, now=1_000_000)
        record_gas_spend(5.0, now=1_000_000 + 3600)
        record_gas_spend(2.0, now=1_000_000 + 5000)
        with self.assertRaises(GateDenied):
            check_gate({"expected_net_usd": 50.0, "notional_usd": 100},
                       CFG, now=1_000_000 + 7200)
        # 24h rollover clears the cap
        check_gate({"expected_net_usd": 50.0, "notional_usd": 100},
                   CFG, now=1_000_000 + 90000)


class TestEncoding(unittest.TestCase):
    def test_steps_encoding_layout(self):
        step = {"kind": 0, "target": "0x" + "11" * 20,
                "tokenIn": "0x" + "22" * 20, "tokenOut": "0x" + "33" * 20,
                "account": "0x" + "44" * 20, "amount": 25_000_000,
                "limit": 0, "fee": 3000, "recipientMode": 1}
        out = encode_steps([step])
        body = bytes.fromhex(out)
        # array offset word = 160 (after 4 scalar args + the offset itself)
        self.assertEqual(len(body), 32 * (2 + 9))
        self.assertEqual(int.from_bytes(body[0:32], "big"), 160)
        self.assertEqual(int.from_bytes(body[32:64], "big"), 1)
        # first word of step 0 is kind
        self.assertEqual(int.from_bytes(body[64:96], "big"), 0)
        # fee word (7th) is 3000
        self.assertEqual(int.from_bytes(body[64 + 7 * 32:64 + 8 * 32], "big"),
                         3000)

    def test_run_calldata_selector(self):
        data = build_run_calldata(
            "aave", "0x" + "aa" * 20, 25_000_000, 100, 1_700_000_000,
            [{"kind": 0, "target": "0x" + "11" * 20,
              "tokenIn": "0x" + "22" * 20, "tokenOut": "0x" + "33" * 20,
              "account": "0x" + "44" * 20, "amount": 25_000_000,
              "limit": 0, "fee": 3000, "recipientMode": 0}])
        from zero.keccak import keccak256
        want = keccak256(
            b"run(address,uint256,uint256,uint256,(uint8,address,address,"
            b"address,uint256,uint256,uint24,uint8)[])").hex()[:8]
        self.assertEqual(data[:4].hex(), want)


if __name__ == "__main__":
    unittest.main()