import json
import os
import threading
import time
import unittest

from zero.candidate import ArbitrageCandidate


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")
BASE = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
QUOTE = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"


class FakeRpc:
    def block_number(self):
        return 999


class FakeEngine:
    def __init__(self):
        self.rpc = FakeRpc()


class FakeLedger:
    def record(self, **kwargs):
        return 1


class TestSwarmVerifier(unittest.TestCase):
    def _config(self):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def _candidate(self, predicted_net=0.10):
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
            predicted_net=predicted_net,
            min_profit=0.05,
        ).as_dict()

    def _row(self, route_id="r1", expected=0.10):
        return {
            "block": 999,
            "route_id": route_id,
            "worker_id": "A1",
            "manager_id": "ALPHA",
            "size": 100.0,
            "loan_notional_usd": 100.0,
            "gross": 0.20,
            "gross_usd": 0.20,
            "flash_fee_usd": 0.05,
            "gas_usd": 0.04,
            "model_reserve_usd": 0.01,
            "expected_net_usd": expected,
            "decision": "PASS" if expected > 0 else "REJECT",
            "reason": "ok" if expected > 0 else "expected_net_usd <= 0",
            "candidate": self._candidate(expected) if expected > 0 else None,
        }

    def test_positive_candidate_is_encoded_and_fork_verified(self):
        try:
            from zero.swarm import SwarmSupervisor
        except ImportError as exc:
            self.fail(f"SwarmSupervisor is missing: {exc}")

        calls = []

        def verifier(payload):
            calls.append(payload)
            return 0

        def runner(worker, allocator, context):
            return [self._row()] if worker.worker_id == "A1" else []

        result = SwarmSupervisor(
            FakeEngine(), self._config(), FakeLedger(), verifier=verifier,
            worker_runner=runner,
            catalog_builder=lambda block, workers: ([], object()),
        ).run_block(999)

        self.assertEqual(result["fork_verifications_attempted"], 1)
        self.assertEqual(result["fork_verifications_passed"], 1)
        self.assertEqual(result["fork_verifications_failed"], 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["candidate"]["block"], 999)
        self.assertEqual(len(calls[0]["steps"]), 3)

    def test_zero_or_negative_candidates_are_never_verified(self):
        calls = []

        def verifier(payload):
            calls.append(payload)
            return 0

        def runner(worker, allocator, context):
            return [self._row(expected=0.0)] if worker.worker_id == "A1" else []

        from zero.swarm import SwarmSupervisor
        result = SwarmSupervisor(
            FakeEngine(), self._config(), FakeLedger(), verifier=verifier,
            worker_runner=runner,
            catalog_builder=lambda block, workers: ([], object()),
        ).run_block(999)
        self.assertEqual(result["fork_verifications_attempted"], 0)
        self.assertEqual(calls, [])

    def test_verifier_failure_and_exception_do_not_abort_swarm(self):
        state = {"calls": 0}

        def verifier(payload):
            state["calls"] += 1
            if state["calls"] == 1:
                return 1
            raise RuntimeError("fork unavailable")

        def runner(worker, allocator, context):
            if worker.worker_id != "A1":
                return []
            return [self._row("r1", 0.20), self._row("r2", 0.10)]

        from zero.swarm import SwarmSupervisor
        result = SwarmSupervisor(
            FakeEngine(), self._config(), FakeLedger(), verifier=verifier,
            worker_runner=runner,
            catalog_builder=lambda block, workers: ([], object()),
        ).run_block(999)
        self.assertEqual(result["positive_net"], 2)
        self.assertEqual(result["fork_verifications_attempted"], 2)
        self.assertEqual(result["fork_verifications_passed"], 0)
        self.assertEqual(result["fork_verifications_failed"], 2)

    def test_default_fork_concurrency_is_one(self):
        cfg = self._config()
        cfg["swarm"]["max_fork_concurrency"] = 1
        lock = threading.Lock()
        active = 0
        maximum = 0

        def verifier(payload):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return 0

        def runner(worker, allocator, context):
            if worker.worker_id != "A1":
                return []
            return [self._row("r1", 0.20), self._row("r2", 0.10)]

        from zero.swarm import SwarmSupervisor
        result = SwarmSupervisor(
            FakeEngine(), cfg, FakeLedger(), verifier=verifier,
            worker_runner=runner,
            catalog_builder=lambda block, workers: ([], object()),
        ).run_block(999)
        self.assertEqual(result["fork_verifications_passed"], 2)
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
