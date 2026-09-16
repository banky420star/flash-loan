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
BLOCK="${FORK_BLOCK:-$(cast block-number --rpc-url "$RPC_URL")}" 
echo "ZERO hardened executor fork - Arbitrum block $BLOCK"
forge test --match-contract ZeroExecutorForkTest \
  --fork-url "$RPC_URL" --fork-block-number "$BLOCK" -vv
