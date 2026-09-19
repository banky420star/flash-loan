#!/usr/bin/env python3
"""Bridge the hot wallet's mainnet ETH to Arbitrum One via the official
Delayed Inbox `depositEth()` (retryable-free plain deposit, credits the
sender's L2 address). Verifies the inbox contract on-chain before signing.

Run from repo root:  python3 scripts/bridge_eth_to_arbitrum.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zero.keccak import keccak256
from zero.wallet import sign_transaction, send_raw_transaction, \
    private_key_to_address
from zero.rpc import Rpc

INBOX = "0x4Dbd4fc535Ac27206064B68FfCf827b0A60BAB3f"   # docs.arbitrum.io, verified on-chain below
MAINNET = "https://ethereum-rpc.publicnode.com"
KEY_PATH = Path(__file__).resolve().parent.parent / "wallet" / "hot.key"
GAS_LIMIT = 150_000
GAS_BUFFER_WEI = 10**12          # keep 0.000001 ETH spare for gas rounding


def curl_rpc(url, method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": params}).encode()
    for _ in range(3):
        out = subprocess.run(
            ["curl", "-s", "--max-time", "15", "-X", "POST",
             "-H", "Content-Type: application/json",
             "-H", "User-Agent: zero-bridge/1.0",
             "--data", payload, url],
            capture_output=True, text=True).stdout
        try:
            r = json.loads(out)
            if "result" in r and r["result"] is not None:
                return r["result"]
            print(f"  rpc note: {r.get('error')}")
        except Exception as e:
            print(f"  rpc fail: {e}")
        time.sleep(2)
    raise SystemExit("RPC unreachable")


def main():
    key = bytes.fromhex(KEY_PATH.read_text().strip())
    addr = private_key_to_address(key)
    print(f"hot wallet: {addr}")

    # 1. Verify the inbox is a real contract on mainnet.
    code = curl_rpc(MAINNET, "eth_getCode", [INBOX, "latest"])
    assert isinstance(code, str) and len(code) > 4, "inbox has no code"
    print(f"inbox verified on-chain: {len(code)//2 - 1} bytes of code")

    bal_hex = curl_rpc(MAINNET, "eth_getBalance", [addr, "latest"])
    bal = int(bal_hex, 16)
    print(f"mainnet balance: {bal/1e18:.6f} ETH")
    assert bal > 0, "nothing to bridge"

    nonce = int(curl_rpc(MAINNET, "eth_getTransactionCount",
                         [addr, "pending"]), 16)
    gas_price = int(curl_rpc(MAINNET, "eth_gasPrice", []), 16)
    print(f"nonce {nonce}, gas {gas_price/1e9:.2f} gwei")

    gas_cost = GAS_LIMIT * gas_price
    value = bal - gas_cost - GAS_BUFFER_WEI
    assert value > 10**14, f"value {value} too small after gas budget"
    print(f"bridging {value/1e18:.6f} ETH "
          f"(gas budget {gas_cost/1e18:.6f} + buffer)")

    # 2. Estimate gas with the real value; abort if it needs more than budgeted.
    selector = "0x" + keccak256(b"depositEth()").hex()[:8]
    est = curl_rpc(MAINNET, "eth_estimateGas",
                   [{"from": addr, "to": INBOX, "value": hex(value),
                     "data": selector}])
    est_gas = int(est, 16)
    print(f"estimated gas: {est_gas}")
    assert est_gas * gas_price < bal, "estimate exceeds balance"

    rpc = Rpc(MAINNET)
    raw = sign_transaction(key, nonce=nonce,
                           gas_price_gwei=gas_price / 1e9 * 1.2,
                           gas_limit=int(est_gas * 1.3) + 1,
                           to=INBOX, value_wei=value,
                           data=bytes.fromhex(selector[2:]), chain_id=1)
    tx_hash = send_raw_transaction(rpc, raw)
    print(f"broadcast: {tx_hash}")

    # 3. Wait for the mainnet receipt.
    for _ in range(60):
        time.sleep(5)
        rec = curl_rpc(MAINNET, "eth_getTransactionReceipt", [tx_hash])
        if rec:
            print(f"receipt: status={rec['status']} "
                  f"block={int(rec['blockNumber'],16)}")
            assert rec["status"] == "0x1", "bridge tx FAILED on mainnet"
            break
    else:
        raise SystemExit("no receipt in 5 minutes — check manually")
    print("DONE — ETH deposited to Arbitrum Delayed Inbox; "
          "it credits the hot wallet on Arbitrum One within ~15 min")


if __name__ == "__main__":
    main()