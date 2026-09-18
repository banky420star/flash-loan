#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Foundry is only needed for fork verification, not to start the engine, so
# resolve it without failing when it is not installed at all.
. "$ROOT/scripts/foundry_env.sh"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${ZERO_RPC_URL:=https://arb1.arbitrum.io/rpc}"
: "${ZERO_RPC_URLS:=$ZERO_RPC_URL}"
: "${ZERO_RPC_COOLDOWN_S:=5}"
: "${ZERO_LEDGER_PATH:=zero_ledger.db}"
: "${ZERO_SWARM_INTERVAL:=5}"
: "${ZERO_MAX_RPC_BATCH:=20}"
: "${ZERO_MAX_RPC_CONCURRENCY:=8}"
: "${ZERO_RUNTIME_STATUS_PATH:=run/zero-status.json}"
: "${ZERO_LOG_PATH:=run/swarm.log}"
export ZERO_RPC_URL ZERO_RPC_URLS ZERO_RPC_COOLDOWN_S ZERO_LEDGER_PATH
export ZERO_SWARM_INTERVAL ZERO_MAX_RPC_BATCH ZERO_MAX_RPC_CONCURRENCY
export ZERO_RUNTIME_STATUS_PATH ZERO_LOG_PATH

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }

case "${1:-once}" in
  doctor) python3 -m zero.cli doctor ;;
  health) python3 -m zero.cli health --status "$ZERO_RUNTIME_STATUS_PATH" ;;
  once) python3 -m zero.cli swarm-once --ledger "$ZERO_LEDGER_PATH" ;;
  run)
    mkdir -p "$(dirname "$ZERO_LOG_PATH")"
    python3 -u -m zero.cli swarm --interval "$ZERO_SWARM_INTERVAL" --ledger "$ZERO_LEDGER_PATH" 2>&1 | tee -a "$ZERO_LOG_PATH"
    ;;
  tui) python3 -m zero.tui control ;;
  process-tui) python3 -m zero.tui process ;;
  test)
    bash scripts/cli_test.sh
    command -v forge >/dev/null || { echo "forge is required for build verification" >&2; exit 2; }
    forge build
    ;;
  *)
    echo "usage: $0 {doctor|health|once|run|tui|process-tui|test}" >&2
    exit 2
    ;;
esac
