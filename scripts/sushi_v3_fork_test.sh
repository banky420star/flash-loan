#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "$0")/foundry_env.sh"

if ! command -v forge >/dev/null 2>&1; then
  echo "ERROR: forge is required. Install Foundry from https://getfoundry.sh" >&2
  exit 2
fi

RPC_URL="${ARBITRUM_RPC_URL:-https://arb1.arbitrum.io/rpc}"
# Python encodes the Sushi unwind leg the executor will send; export the
# exact bytes so the fork test proves them on the live router.
ENCODED="$(python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from zero.calldata import SUSHI_V3_ROUTER, _exact_input_single
from zero.keccak import selector_hex
from zero.rpc import encode_address, encode_uint
# MSG_SENDER sentinel is a placeholder the executor rewrites to itself,
# because the Sushi router does not support the sentinel (it would burn).
APPROVE = "approve(address,uint256)"
approve_data = _join = None
def _join(sig, words):
    return selector_hex(sig) + "".join(w.removeprefix("0x") for w in words)
approve_data = _join(APPROVE, [
    encode_address(SUSHI_V3_ROUTER), encode_uint(5 * 10**16)])
swap_data = _exact_input_single(
    token_in="0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
    token_out="0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8",
    fee=3000,
    recipient="0x0000000000000000000000000000000000000001",
    amount_in=5 * 10**16,  # 0.05 WETH, matches the SWAP_IN constant
    amount_out_minimum=0,
    deadline=1 << 128,
)
assert swap_data.startswith(selector_hex(
    "exactInputSingle((address,address,uint24,address,"
    "uint256,uint256,uint256,uint160))"))
print(approve_data, swap_data)
PY
)"
export ZERO_APPROVE_DATA="$(echo "$ENCODED" | cut -d' ' -f1)"
export ZERO_SWAP_DATA="$(echo "$ENCODED" | awk '{print $2}')"
ARGS=(test --match-contract ZeroSushiV3SwapForkTest --fork-url "$RPC_URL" -vv)
if [[ -n "${FORK_BLOCK:-}" ]]; then
  ARGS+=(--fork-block-number "$FORK_BLOCK")
fi

forge "${ARGS[@]}"