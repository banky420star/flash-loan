from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import tempfile
import threading
import time


@dataclass(frozen=True)
class RuntimeStatus:
    heartbeat_at: float = 0.0
    process_pid: int | None = None
    process_started_at: float | None = None
    cycle_started_at: float | None = None
    cycle_phase: str = "idle"
    cycle_number: int = 0
    last_cycle_completed_at: float | None = None
    block: int | None = None
    chain_head: int | None = None
    block_lag: int | None = None
    active_workers: int = 0
    routes_scanned: int = 0
    worker_failures: int = 0
    positive_net: int = 0
    best_expected_net: float | None = None
    fork_attempted: int = 0
    fork_passed: int = 0
    fork_failed: int = 0
    elapsed_s: float = 0.0
    catalog_ms: float = 0.0
    scan_ms: float = 0.0
    verify_ms: float = 0.0
    consecutive_errors: int = 0
    last_error: str | None = None
    rpc_endpoint: str | None = None
    kill_state: bool = False
    kill_reason: str | None = None


class RuntimeMonitor:
    """Atomic observability snapshots; never a source of trading state."""

    def __init__(self, path: str, *, clock=None):
        self.path = Path(path)
        self.clock = clock or time.time
        self.status = RuntimeStatus()
        self._lock = threading.RLock()

    def _write_locked(self, status: RuntimeStatus) -> RuntimeStatus:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(status), sort_keys=True, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        self.status = status
        return status

    def _update(self, **changes) -> RuntimeStatus:
        with self._lock:
            return self._write_locked(replace(self.status, **changes))

    def record_process_start(self, *, pid: int | None = None) -> RuntimeStatus:
        now = float(self.clock())
        return self._update(
            heartbeat_at=now,
            process_pid=int(pid if pid is not None else os.getpid()),
            process_started_at=now,
            cycle_phase="starting",
        )

    def record_cycle_start(self, *, block: int | None = None,
                           rpc_endpoint: str | None = None) -> RuntimeStatus:
        now = float(self.clock())
        return self._update(
            heartbeat_at=now,
            cycle_started_at=now,
            cycle_phase="scanning",
            cycle_number=self.status.cycle_number + 1,
            block=(int(block) if block is not None else self.status.block),
            rpc_endpoint=rpc_endpoint or self.status.rpc_endpoint,
        )

    def heartbeat(self, *, phase: str | None = None) -> RuntimeStatus:
        changes = {"heartbeat_at": float(self.clock())}
        if phase is not None:
            changes["cycle_phase"] = str(phase)
        return self._update(**changes)

    def record_cycle(self, result: dict, *, chain_head: int | None = None,
                     rpc_endpoint: str | None = None) -> RuntimeStatus:
        now = float(self.clock())
        block = int(result.get("block", 0) or 0)
        head = int(chain_head) if chain_head is not None else block
        lag = max(0, head - block) if block else None
        return self._update(
            heartbeat_at=now,
            last_cycle_completed_at=now,
            cycle_phase="sleeping",
            block=block or None,
            chain_head=head or None,
            block_lag=lag,
            active_workers=int(result.get("active_workers", 0) or 0),
            routes_scanned=int(result.get("routes_scanned", 0) or 0),
            worker_failures=int(result.get("worker_failures", 0) or 0),
            positive_net=int(result.get("positive_net", 0) or 0),
            best_expected_net=(float(result["best_expected_net"])
                if result.get("best_expected_net") is not None else None),
            fork_attempted=int(result.get("fork_verifications_attempted", 0) or 0),
            fork_passed=int(result.get("fork_verifications_passed", 0) or 0),
            fork_failed=int(result.get("fork_verifications_failed", 0) or 0),
            elapsed_s=float(result.get("elapsed_s", 0.0) or 0.0),
            catalog_ms=float(result.get("catalog_ms", 0.0) or 0.0),
            scan_ms=float(result.get("scan_ms", 0.0) or 0.0),
            verify_ms=float(result.get("verify_ms", 0.0) or 0.0),
            consecutive_errors=0,
            last_error=None,
            rpc_endpoint=rpc_endpoint or self.status.rpc_endpoint,
        )

    def record_error(self, error: Exception | str, *,
                     rpc_endpoint: str | None = None) -> RuntimeStatus:
        message = (f"{type(error).__name__}: {error}"
                   if isinstance(error, Exception) else str(error))
        return self._update(
            heartbeat_at=float(self.clock()),
            cycle_phase="error",
            consecutive_errors=self.status.consecutive_errors + 1,
            last_error=message,
            rpc_endpoint=rpc_endpoint or self.status.rpc_endpoint,
        )

    def set_kill_state(self, killed: bool, *, reason: str | None = None) -> RuntimeStatus:
        return self._update(
            heartbeat_at=float(self.clock()),
            kill_state=bool(killed),
            kill_reason=(str(reason) if reason is not None else None),
        )
