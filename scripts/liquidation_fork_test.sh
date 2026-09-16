#!/usr/bin/env bash
set -euo pipefail

if ! command -v forge >/dev/null 2>&1; then
  echo "ERROR: forge is required. Install Foundry from https://getfoundry.sh" >&2
  exit 2
fi
if ! command -v cast >/dev/null 2>&1; then
  echo "ERROR: cast is required. Install Foundry from https://getfoundry.sh" >&2
  exit 2
fi

RPC_URL="${ARBITRUM_RPC_URL:-https://arb1.arbitrum.io/rpc}"
BLOCK="${FORK_BLOCK:-}"
if [[ -z "$BLOCK" ]]; then
  BLOCK="$(cast block-number --rpc-url "$RPC_URL")"
fi

echo "ZERO deterministic liquidation fork - Arbitrum block $BLOCK"
echo "Fork source only: $RPC_URL"
forge test \
  --match-contract ZeroLiquidationForkTest \
  --fork-url "$RPC_URL" \
  --fork-block-number "$BLOCK" \
  -vv
