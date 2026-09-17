import unittest

from zero.tui_data import MonitoringSnapshot, PnlSummary, ProcessInfo, StrategyPnl
from zero.tui import _line_color_role, render_control, render_process


class TestTuiRendering(unittest.TestCase):
    def snapshot(self):
        pnl = PnlSummary(
            predicted_total=3.50, realized_total=2.30, model_error_total=-1.20,
            session_predicted=2.50, session_realized=1.50,
            day_predicted=3.50, day_realized=2.30,
            measured_successes=2, execution_reverts=1, invalid_harness=4,
            infrastructure_errors=2, best_realized=1.50, worst_realized=0.0,
            realized_curve=(0.0, 0.8, 0.8, 2.3),
            by_strategy={
                "swarm_arbitrage": StrategyPnl(1.5, 0.8, -0.7, 2),
                "swarm_liquidation": StrategyPnl(2.0, 1.5, -0.5, 1),
            },
        )
        process = ProcessInfo(4242, 1, 24.5, 0.4, 3723, "S", "python -m zero.cli swarm")
        return MonitoringSnapshot(
            now=200.0,
            status={"cycle_phase": "scanning", "cycle_number": 7, "block": 500,
                    "chain_head": 505, "block_lag": 5, "active_workers": 20,
                    "routes_scanned": 622, "worker_failures": 2,
                    "positive_net": 1, "rpc_endpoint": "https://rpc-a"},
            process=process, process_alive=True, heartbeat_age_s=2.0,
            cycle_age_s=50.0, pnl=pnl,
            errors=("RPC Error: 403 Forbidden",),
            log_tail=("normal", "RPC Error: 403 Forbidden"),
            recent_forks=({"strategy": "swarm_arbitrage", "predicted_net": 1.0,
                           "realized_net": 0.8, "outcome_class": "measured_success",
                           "block": 501, "created_at": "now", "model_error": -0.2,
                           "success": True},),
        )

    def test_control_floor_contains_simulated_pnl_and_strategy_split(self):
        text = render_control(self.snapshot(), width=110)
        self.assertIn("ZERO CONTROL FLOOR", text)
        self.assertIn("FORK / SHADOW P&L", text)
        self.assertIn("Today realized", text)
        self.assertIn("Arbitrage", text)
        self.assertIn("Liquidation", text)
        self.assertIn("invalid harness", text.lower())
        self.assertIn("SIMULATED", text)

    def test_process_monitor_uses_fresh_heartbeat_when_ps_telemetry_unavailable(self):
        snap = self.snapshot()
        snap = MonitoringSnapshot(
            now=snap.now, status={**snap.status, "process_pid": 4242},
            process=None, process_alive=True, heartbeat_age_s=1.0,
            cycle_age_s=snap.cycle_age_s, pnl=snap.pnl, errors=snap.errors,
            log_tail=snap.log_tail, recent_forks=snap.recent_forks)
        text = render_process(snap, width=110)
        self.assertIn("PROCESS ALIVE", text)
        self.assertIn("PID 4242", text)
        self.assertIn("CPU/RAM unavailable", text)

    def test_line_color_roles_make_health_and_errors_visually_distinct(self):
        self.assertEqual(_line_color_role("PROCESS ALIVE | PID 4242"), "good")
        self.assertEqual(_line_color_role("PROCESS NOT FOUND"), "bad")
        self.assertEqual(_line_color_role("RPC Error: 403 Forbidden"), "bad")
        self.assertEqual(_line_color_role(" ZERO CONTROL FLOOR ----------------"), "accent")
        self.assertEqual(_line_color_role("Fork attempts 0 | Passed 0 | Failed 0"), "normal")

    def test_process_monitor_separates_process_alive_from_cycle_age(self):
        text = render_process(self.snapshot(), width=110)
        self.assertIn("ZERO PROCESS MONITOR", text)
        self.assertIn("PID 4242", text)
        self.assertIn("CPU 24.5%", text)
        self.assertIn("PROCESS ALIVE", text)
        self.assertIn("Cycle phase scanning", text)
        self.assertIn("Heartbeat age 2.0s", text)
        self.assertIn("403 Forbidden", text)


if __name__ == "__main__":
    unittest.main()
