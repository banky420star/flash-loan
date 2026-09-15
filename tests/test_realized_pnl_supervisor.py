import json
import os
import tempfile
import unittest

from zero.candidate import ArbitrageCandidate
from zero.fork import ForkResult
from zero.ledger import Ledger
from zero.pnl import PnlSwarmSupervisor


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")
BASE = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
QUOTE = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"


class FakeRpc:
    def block_number(self):
        return 999


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()


class TestSupervisorForkPersistence(unittest.TestCase):
    def _config(self):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def _candidate(self):
        return ArbitrageCandidate(
            block=999,
            name="test",
            base_asset=BASE,
            quote_asset=QUOTE,
            base_decimals=6,
            quote_decimals=18,
            loan_size=100.0,
            hop1_expected_out=0.05,
            hop2_expected_out=100.20,
            fee1=500,
            fee2=3000,
            flash_premium_bps=5,
            gas_cost_usd=0.04,
            gross_profit=0.20,
            predicted_net=0.10,
            min_profit=0.05,
        ).as_dict()

    def _row(self):
        return {
            "block": 999,
            "route_id": "route-1",
            "worker_id": "A1",
            "manager_id": "ALPHA",
            "size": 100.0,
            "loan_notional_usd": 100.0,
            "gross": 0.20,
            "gross_usd": 0.20,
            "flash_fee_usd": 0.05,
            "gas_usd": 0.04,
            "model_reserve_usd": 0.01,
            "expected_net_usd": 0.10,
            "decision": "PASS",
            "reason": "ok",
            "candidate": self._candidate(),
            "route": {"base_price_usd": 1.0},
        }

    def test_structured_fork_result_is_persisted_on_supervisor_thread(self):
        cfg = self._config()

        def runner(worker, allocator, context):
            return [self._row()] if worker.worker_id == "A1" else []

        seen_payloads = []
        def verifier(payload):
            seen_payloads.append(payload)
            return ForkResult(
                block=999,
                strategy="swarm_arbitrage",
                success=True,
                gas_used=800_000,
                predicted_net=0.11,
                realized_net=0.08,
                detail=json.dumps({"route_id": "route-1"}),
            )

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(os.path.join(tmp, "ledger.db"))
            supervisor = PnlSwarmSupervisor(
                FakeEngine(), cfg, ledger, verifier=verifier,
                worker_runner=runner,
                catalog_builder=lambda block, workers: ([], object()),
            )
            result = supervisor.run_block(999)
            rows = ledger.fork_tail(10)
            ledger.close()

        self.assertEqual(result["fork_verifications_attempted"], 1)
        self.assertEqual(result["fork_verifications_passed"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["block"], 999)
        self.assertEqual(rows[0]["gas_used"], 800_000)
        self.assertAlmostEqual(rows[0]["predicted_net"], 0.11)
        self.assertAlmostEqual(rows[0]["realized_net"], 0.08)
        self.assertAlmostEqual(rows[0]["model_error"], -0.03)
        self.assertEqual(seen_payloads[0]["verification"]["route_id"], "route-1")
        self.assertEqual(seen_payloads[0]["verification"]["gas_limit"], cfg["gas_limit"])
        self.assertAlmostEqual(seen_payloads[0]["verification"]["base_price_usd"], 1.0)
        self.assertAlmostEqual(seen_payloads[0]["verification"]["model_reserve_usd"], 0.01)


if __name__ == "__main__":
    unittest.main()
