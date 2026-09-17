import unittest

from zero import cli


class FakeMonitor:
    def __init__(self):
        self.beats = 0

    def heartbeat(self):
        self.beats += 1


class FakeStop:
    def __init__(self):
        self.calls = 0

    def wait(self, interval):
        self.calls += 1
        return self.calls >= 2


class TestRuntimeHeartbeatLoop(unittest.TestCase):
    def test_background_loop_touches_status_between_cycles(self):
        monitor = FakeMonitor()
        stop = FakeStop()
        cli._runtime_heartbeat_loop(monitor, stop, interval=5.0)
        self.assertEqual(monitor.beats, 1)
        self.assertEqual(stop.calls, 2)


if __name__ == "__main__":
    unittest.main()
