from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionRequest:
    block: int
    candidate_id: str
    loan_notional_usd: float
    gas_usd: float
    expected_net_usd: float
    deadline_ts: float


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


class ExecutionPolicy:
    """Pure risk gate; intentionally contains no network or signing code."""

    def __init__(self, *, max_block_lag: int, max_gas_usd: float,
                 max_loan_notional_usd: float, max_consecutive_reverts: int,
                 max_daily_modeled_loss_usd: float):
        self.max_block_lag=max(0,int(max_block_lag))
        self.max_gas_usd=max(0.0,float(max_gas_usd))
        self.max_loan_notional_usd=max(0.0,float(max_loan_notional_usd))
        self.max_consecutive_reverts=max(1,int(max_consecutive_reverts))
        self.max_daily_modeled_loss_usd=max(0.0,float(max_daily_modeled_loss_usd))
        self._seen=set()
        self.consecutive_reverts=0
        self.daily_modeled_loss_usd=0.0
        self._day=None
        self.kill_state=False
        self.kill_reason=None

    @staticmethod
    def _utc_day(now: float) -> int:
        return int(float(now) // 86_400)

    def _roll_day(self, now: float) -> None:
        day=self._utc_day(now)
        if self._day is None:
            self._day=day
        elif day != self._day:
            self._day=day
            self.daily_modeled_loss_usd=0.0

    def set_kill(self, killed: bool, reason: str | None = None) -> None:
        self.kill_state=bool(killed)
        self.kill_reason=(str(reason) if reason is not None else None)

    def record_outcome(self, success: bool, *, modeled_loss_usd: float,
                       now: float) -> None:
        self._roll_day(now)
        if success:
            self.consecutive_reverts=0
            return
        self.consecutive_reverts += 1
        self.daily_modeled_loss_usd += max(0.0,float(modeled_loss_usd))

    def authorize(self, request: ExecutionRequest, *, chain_head: int,
                  now: float) -> PolicyDecision:
        self._roll_day(now)
        if self.kill_state:
            return PolicyDecision(False,'kill_state')
        if self.consecutive_reverts >= self.max_consecutive_reverts:
            return PolicyDecision(False,'revert_limit_reached')
        if self.daily_modeled_loss_usd >= self.max_daily_modeled_loss_usd:
            return PolicyDecision(False,'daily_loss_limit_reached')
        key=(int(request.block),str(request.candidate_id))
        if key in self._seen:
            return PolicyDecision(False,'duplicate_candidate')
        if int(chain_head) - int(request.block) > self.max_block_lag:
            return PolicyDecision(False,'stale_block')
        if float(now) > float(request.deadline_ts):
            return PolicyDecision(False,'deadline_expired')
        if float(request.expected_net_usd) <= 0:
            return PolicyDecision(False,'non_positive_net')
        if float(request.gas_usd) > self.max_gas_usd:
            return PolicyDecision(False,'gas_limit_exceeded')
        if float(request.loan_notional_usd) > self.max_loan_notional_usd:
            return PolicyDecision(False,'loan_limit_exceeded')
        self._seen.add(key)
        return PolicyDecision(True,'ok')
