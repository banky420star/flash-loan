#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "$0")/foundry_env.sh"

if ! command -v forge >/dev/null 2>&1; then
  echo "ERROR: forge is required. Install Foundry from https://getfoundry.sh" >&2
  exit 2
fi

RPC_URL="${ARBITRUM_RPC_URL:-https://arb1.arbitrum.io/rpc}"
ARGS=(test --match-contract ZeroForkExecutorForkTest --fork-url "$RPC_URL" -vv)
if [[ -n "${FORK_BLOCK:-}" ]]; then
  ARGS+=(--fork-block-number "$FORK_BLOCK")
fi

forge "${ARGS[@]}"
