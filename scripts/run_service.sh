#!/usr/bin/env bash
set -euo pipefail

cd "${ZERO_WORKDIR:-/opt/zero}"
: "${ZERO_SWARM_INTERVAL:=5}"
: "${ZERO_LEDGER_PATH:=/var/lib/zero/zero_ledger.db}"
: "${ZERO_RUNTIME_STATUS_PATH:=/var/lib/zero/runtime-status.json}"
: "${ZERO_MAX_RPC_BATCH:=20}"
: "${ZERO_MAX_RPC_CONCURRENCY:=8}"
export ZERO_SWARM_INTERVAL ZERO_LEDGER_PATH ZERO_RUNTIME_STATUS_PATH
export ZERO_MAX_RPC_BATCH ZERO_MAX_RPC_CONCURRENCY

exec python3 -u -m zero.cli swarm \
  --interval "$ZERO_SWARM_INTERVAL" \
  --ledger "$ZERO_LEDGER_PATH"
