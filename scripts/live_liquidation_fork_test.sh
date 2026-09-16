#!/usr/bin/env bash
set -euo pipefail

if ! command -v forge >/dev/null 2>&1; then
  echo "ERROR: forge is required. Install Foundry from https://getfoundry.sh" >&2
  exit 2
fi

RPC_URL="${ARBITRUM_RPC_URL:-https://arb1.arbitrum.io/rpc}"
: "${FORK_BLOCK:?FORK_BLOCK is required for exact liquidation replay}"
: "${ZERO_ASSET:?ZERO_ASSET is required}"
: "${ZERO_LOAN_RAW:?ZERO_LOAN_RAW is required}"
: "${ZERO_MIN_PROFIT_RAW:?ZERO_MIN_PROFIT_RAW is required}"
for i in 0 1 2; do
  target_var="ZERO_STEP${i}_TARGET"
  data_var="ZERO_STEP${i}_DATA"
  : "${!target_var:?${target_var} is required}"
  : "${!data_var:?${data_var} is required}"
done
: "${ZERO_STEP3_TARGET:?ZERO_STEP3_TARGET is required}"
: "${ZERO_STEP3_DATA:?ZERO_STEP3_DATA is required}"
: "${ZERO_RESULT_PATH:?ZERO_RESULT_PATH is required for structured fork results}"

mkdir -p "$(dirname "$ZERO_RESULT_PATH")"
rm -f "$ZERO_RESULT_PATH"

echo "ZERO liquidation exact candidate replay - Arbitrum block $FORK_BLOCK"
echo "Fork source only: $RPC_URL"

forge test \
  --match-contract ZeroLiveLiquidationForkTest \
  --fork-url "$RPC_URL" \
  --fork-block-number "$FORK_BLOCK" \
  -vv
