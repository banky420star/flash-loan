#!/usr/bin/env bash
set -euo pipefail

if ! command -v forge >/dev/null 2>&1; then
  echo "ERROR: forge is required. Install Foundry from https://getfoundry.sh" >&2
  exit 2
fi

RPC_URL="${ARBITRUM_RPC_URL:-https://arb1.arbitrum.io/rpc}"
: "${FORK_BLOCK:?FORK_BLOCK is required for exact candidate replay}"
: "${ZERO_ASSET:?ZERO_ASSET is required}"
: "${ZERO_LOAN_RAW:?ZERO_LOAN_RAW is required}"
: "${ZERO_MIN_PROFIT_RAW:?ZERO_MIN_PROFIT_RAW is required}"
: "${ZERO_STEP0_TARGET:?ZERO_STEP0_TARGET is required}"
: "${ZERO_STEP0_DATA:?ZERO_STEP0_DATA is required}"
: "${ZERO_STEP1_TARGET:?ZERO_STEP1_TARGET is required}"
: "${ZERO_STEP1_DATA:?ZERO_STEP1_DATA is required}"
: "${ZERO_STEP2_TARGET:?ZERO_STEP2_TARGET is required}"
: "${ZERO_STEP2_DATA:?ZERO_STEP2_DATA is required}"

echo "ZERO v0.4 exact candidate replay — Arbitrum block $FORK_BLOCK"
echo "Fork source only: $RPC_URL"

forge test \
  --match-contract ZeroLiveCandidateForkTest \
  --fork-url "$RPC_URL" \
  --fork-block-number "$FORK_BLOCK" \
  -vv
