import unittest

from zero.swarm import ScanContext, SwarmSupervisor, WorkerSpec


class FakeAllocator:
    def __init__(self, route):
        self.route = route

    def next_route(self, worker_id, allow_steal=True):
        route, self.route = self.route, None
        return route

    def claim(self, worker_id, route):
        return True


class FakeEngine:
    def __init__(self):
        self.calls = []

    def scan_multidex_route(self, block, route, **kwargs):
        self.calls.append((block, route["route_id"], kwargs))
        return {"block": block, "route_id": route["route_id"],
                "expected_net_usd": 0.0, "candidate": None,
                "decision": "REJECT"}


class TestMultiDexSwarm(unittest.TestCase):
    def test_worker_dispatches_exact_venue_route_to_multidex_scanner(self):
        supervisor = object.__new__(SwarmSupervisor)
        supervisor.engine = FakeEngine()
        supervisor.config = {"swarm": {
            "work_stealing": True, "model_reserve_usd": 0.25}}
        worker = WorkerSpec("A1", "ALPHA", ("USDC", "WETH"), "test")
        allocator = FakeAllocator({
            "route_kind": "multidex_exact", "route_id": "route-1"})
        context = ScanContext(
            block=777, aave_pool="pool", oracle="oracle",
            premium_bps=5, eth_price_usd=2000.0, gas_usd=0.1,
            tokens={}, pool_states={})

        out = supervisor._run_worker(worker, allocator, context)

        self.assertEqual(out["routes_scanned"], 1)
        self.assertEqual(len(out["route_errors"]), 0)
        self.assertEqual(len(out["rows"]), 1)
        self.assertEqual(supervisor.engine.calls[0][0], 777)
        self.assertEqual(supervisor.engine.calls[0][1], "route-1")
        self.assertEqual(supervisor.engine.calls[0][2]["model_reserve_usd"], 0.25)


if __name__ == "__main__":
    unittest.main()
