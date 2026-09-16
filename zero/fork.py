"""Local-only Arbitrum fork process helpers.

This module may construct and inspect an Anvil fork, but it never authorizes
writes to a remote RPC endpoint. Any write-capable integration must call
``assert_local_write_target`` first.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from urllib.parse import urlparse


ARBITRUM_ONE_CHAIN_ID = 42161

OUTCOME_MEASURED_SUCCESS = "measured_success"
OUTCOME_EXECUTION_REVERT = "execution_revert"
OUTCOME_INFRASTRUCTURE_ERROR = "infrastructure_error"
OUTCOME_INVALID_HARNESS = "invalid_harness"
FORK_OUTCOME_CLASSES = {
    OUTCOME_MEASURED_SUCCESS,
    OUTCOME_EXECUTION_REVERT,
    OUTCOME_INFRASTRUCTURE_ERROR,
    OUTCOME_INVALID_HARNESS,
}


@dataclass(frozen=True)
class ForkResult:
    block: int
    strategy: str
    success: bool
    gas_used: int
    predicted_net: float
    realized_net: float
    detail: str = ""
    outcome_class: str = OUTCOME_MEASURED_SUCCESS

    def __post_init__(self) -> None:
        if self.outcome_class not in FORK_OUTCOME_CLASSES:
            raise ValueError(f"unknown fork outcome class: {self.outcome_class}")
        if self.success != (self.outcome_class == OUTCOME_MEASURED_SUCCESS):
            raise ValueError("success must be true only for measured_success")

    @property
    def reserve_eligible(self) -> bool:
        return self.outcome_class in {
            OUTCOME_MEASURED_SUCCESS, OUTCOME_EXECUTION_REVERT
        }

    @property
    def model_error(self) -> float:
        return self.realized_net - self.predicted_net

    def as_dict(self) -> dict:
        out = asdict(self)
        out["model_error"] = self.model_error
        return out


class ForkSafetyError(RuntimeError):
    """Raised when a write target is not a loopback RPC endpoint."""


def is_loopback_rpc(url: str) -> bool:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def assert_local_write_target(url: str) -> str:
    if not is_loopback_rpc(url):
        raise ForkSafetyError(f"fork writes require loopback RPC, got: {url}")
    return url


@dataclass(frozen=True)
class AnvilFork:
    upstream_rpc: str
    block_number: int
    host: str = "127.0.0.1"
    port: int = 8545
    chain_id: int = ARBITRUM_ONE_CHAIN_ID
    executable: str = "anvil"

    @property
    def local_rpc_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def installed(self) -> bool:
        return shutil.which(self.executable) is not None

    def start(self):
        if not self.installed():
            raise FileNotFoundError(f"{self.executable} is not installed")
        assert_local_write_target(self.local_rpc_url)
        return subprocess.Popen(
            self.command(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def command(self) -> list[str]:
        return [
            self.executable,
            "--fork-url", self.upstream_rpc,
            "--fork-block-number", str(self.block_number),
            "--host", self.host,
            "--port", str(self.port),
            "--chain-id", str(self.chain_id),
            "--silent",
        ]
