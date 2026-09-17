#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
: "${ZERO_RUNTIME_STATUS_PATH:=run/zero-status.json}"
: "${ZERO_LEDGER_PATH:=zero_ledger.db}"
: "${ZERO_LOG_PATH:=run/swarm.log}"
export ZERO_RUNTIME_STATUS_PATH ZERO_LEDGER_PATH ZERO_LOG_PATH
exec python3 -m zero.tui control "$@"
