import json
import os
import tempfile
import unittest

from zero.runtime import RuntimeMonitor


class TestRuntimeProgress(unittest.TestCase):
    def test_long_cycle_heartbeat_preserves_process_and_cycle_state(self):
        times = iter([100.0, 101.0, 106.0, 112.0])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "status.json")
            monitor = RuntimeMonitor(path, clock=lambda: next(times))
            process = monitor.record_process_start(pid=4242)
            started = monitor.record_cycle_start(block=500, rpc_endpoint="rpc-a")
            beat = monitor.heartbeat(phase="scanning")
            completed = monitor.record_cycle({
                "block": 500,
                "active_workers": 20,
                "routes_scanned": 622,
                "worker_failures": 0,
                "positive_net": 0,
                "elapsed_s": 11.0,
            }, chain_head=505, rpc_endpoint="rpc-a")
            with open(path) as handle:
                raw = json.load(handle)

        self.assertEqual(process.process_pid, 4242)
        self.assertEqual(started.cycle_number, 1)
        self.assertEqual(started.cycle_phase, "scanning")
        self.assertEqual(started.cycle_started_at, 101.0)
        self.assertEqual(beat.heartbeat_at, 106.0)
        self.assertEqual(beat.cycle_started_at, 101.0)
        self.assertEqual(completed.cycle_phase, "sleeping")
        self.assertEqual(completed.last_cycle_completed_at, 112.0)
        self.assertEqual(raw["process_pid"], 4242)
