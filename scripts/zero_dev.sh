#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

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
export ZERO_RPC_URL ZERO_LEDGER_PATH ZERO_SWARM_INTERVAL ZERO_MAX_RPC_BATCH ZERO_MAX_RPC_CONCURRENCY

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }

case "${1:-once}" in
  doctor) python3 -m zero.cli doctor ;;
  health) python3 -m zero.cli health --status "$ZERO_RUNTIME_STATUS_PATH" ;;
  once) python3 -m zero.cli swarm-once --ledger "$ZERO_LEDGER_PATH" ;;
  run) python3 -m zero.cli swarm --interval "$ZERO_SWARM_INTERVAL" --ledger "$ZERO_LEDGER_PATH" ;;
  test)
    bash scripts/cli_test.sh
    command -v forge >/dev/null || { echo "forge is required for build verification" >&2; exit 2; }
    forge build
    ;;
  *)
    echo "usage: $0 {doctor|once|run|test}" >&2
    exit 2
    ;;
esac
