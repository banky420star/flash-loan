#!/usr/bin/env python3
"""Deploy ZeroExecutor to Arbitrum One with the hot wallet.

Prerequisites:
  forge build                # produces out/ZeroExecutor.sol/ZeroExecutor.json
  wallet/hot.key funded with gas ETH (0.02-0.05 ETH is plenty)

Prints the deployed contract address; nothing else. The private key never
leaves wallet/hot.key and is never printed.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zero.rpc import Rpc
from zero.wallet import sign_transaction, send_raw_transaction

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config" / "arbitrum.json").read_text())
KEY_PATH = ROOT / "wallet" / "hot.key"
BYTECODE_PATH = ROOT / "out" / "ZeroExecutor.sol" / "ZeroExecutor.json"

# Aave V3 Arbitrum pool — the executor's constructor arg (lender of last
# resort for direct flash loans).
AAVE_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"


def main() -> None:
    if not KEY_PATH.exists():
        sys.exit("no wallet/hot.key — run scripts/generate_hot_wallet.py first")
    if not BYTECODE_PATH.exists():
        sys.exit(f"missing {BYTECODE_PATH} — run `forge build` in contracts/")
    key = bytes.fromhex(KEY_PATH.read_text().strip())

    rpc = None
    for url in CONFIG["rpc_urls"]:
        try:
            candidate = Rpc(url)
            cid = int(candidate.call("eth_chainId", []), 16)
            if cid == CONFIG["chain_id"]:
                rpc = candidate
                break
        except Exception:
            continue
    if rpc is None:
        sys.exit("no working RPC endpoint")
    chain_id = CONFIG["chain_id"]
    assert chain_id == CONFIG["chain_id"], f"chain {chain_id} != 42161"

    from zero.wallet import private_key_to_address
    sender = private_key_to_address(key)
    eth = rpc.call("eth_getBalance", [sender, "latest"])
    print(f"deployer {sender} balance {int(eth, 16)/1e18:.6f} ETH")
    if int(eth, 16) == 0:
        sys.exit("wallet has no gas ETH yet — fund it first")

    artifact = json.loads(BYTECODE_PATH.read_text())
    creation = artifact["bytecode"]["object"]
    # constructor(address pool_) — 32-byte left-padded word
    ctor = AAVE_POOL[2:].lower().rjust(64, "0")
    data = bytes.fromhex(creation + ctor)

    nonce = int(rpc.call("eth_getTransactionCount", [sender, "pending"]), 16)
    gas_price_gwei = max(int(rpc.call("eth_gasPrice", []), 16) / 1e9 * 2, 0.01)
    est = int(rpc.call("eth_estimateGas",
                       [{"from": sender, "data": "0x" + data.hex()}]), 16)
    gas_limit = int(est * 1.3)
    print(f"gas estimate {est} -> limit {gas_limit}, price {gas_price_gwei:.4f} gwei")

    raw = sign_transaction(key, nonce=nonce, gas_price_gwei=gas_price_gwei,
                           gas_limit=gas_limit, to=None, value_wei=0,
                           data=data, chain_id=chain_id)
    tx_hash = send_raw_transaction(rpc, raw)
    print(f"deploy tx {tx_hash}")
    for _ in range(60):
        time.sleep(2)
        receipt = rpc.call("eth_getTransactionReceipt", [tx_hash])
        if receipt:
            status = int(receipt["status"], 16)
            addr = receipt["contractAddress"]
            print("deployed" if status == 1 else "FAILED", addr)
            sys.exit(0 if status == 1 else 1)
    sys.exit("receipt not seen after 120s — check the tx on Arbiscan")


if __name__ == "__main__":
    main()