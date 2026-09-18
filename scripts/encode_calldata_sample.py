#!/usr/bin/env python3
"""Emit sample executor calldata for the Solidity cross-check fork test.

Encodes the exact same route the test re-encodes natively; the fork test
asserts the two encodings are byte-identical. Run from repo root:

  export ZERO_RUN_AAVE=$(python3 scripts/encode_calldata_sample.py aave)
  export ZERO_RUN_BALANCER=$(python3 scripts/encode_calldata_sample.py balancer)
  cd contracts && ~/.foundry/bin/forge test --fork-url https://arb1.arbitrum.io/rpc \
      --match-contract ZeroCalldataCrosscheck
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zero.live_executor import build_run_calldata

ASSET = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"   # USDC (6 decimals)
WETH = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
ROUTER_02 = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"

STEP = {
    "kind": 0,
    "target": ROUTER_02,
    "tokenIn": WETH,
    "tokenOut": ASSET,
    "account": "0x" + "00" * 19 + "01",  # MSG_SENDER sentinel (address(1))
    "amount": 25_000_000 * 10**6,        # 25M USDC
    "limit": 1_000_000 * 10**6,
    "fee": 3000,
    "recipientMode": 1,
}


def main() -> None:
    entry = sys.argv[1] if len(sys.argv) > 1 else "aave"
    data = build_run_calldata(entry, ASSET, 25_000_000 * 10**6, 500,
                              1_700_000_000, [STEP])
    print("0x" + data.hex())


if __name__ == "__main__":
    main()