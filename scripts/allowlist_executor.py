#!/usr/bin/env python3
"""Set ZeroExecutor allowlists from the owner (hot wallet).

Tokens, routers, swap selectors, caller, Balancer vault, per-asset max loans.
Addresses resolved live: tokens from the Aave registry, routers from
config/arbitrum.json. Run from repo root:
  python3 scripts/allowlist_executor.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zero.keccak import keccak256
from zero.wallet import sign_transaction, send_raw_transaction, \
    private_key_to_address
from zero.rpc import Rpc
from zero.aave import AaveV3
from zero.swarm import build_token_registry
from zero.calldata import EXACT_INPUT_SINGLE, EXACT_INPUT_SINGLE_DEADLINE

EXECUTOR = "0x78de834b65d26d35993e0c7ce5f2fc1ca7e461c9"
KEY_PATH = Path(__file__).resolve().parent.parent / "wallet" / "hot.key"
RPCS = ["https://arbitrum-one.public.blastapi.io", "https://arb1.arbitrum.io/rpc"]
PROVIDER = "0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb"

TOKENS = ["WETH", "USDC", "DAI", "USD₮0", "WBTC", "ARB", "LINK",
          "wstETH", "rETH", "weETH", "ezETH", "rsETH"]

# Per-asset on-chain loan backstops (the Python gate caps notional far tighter).
MAX_LOAN = {
    "WETH": 50 * 10**18,
    "USDC": 50_000 * 10**6,
    "DAI": 50_000 * 10**18,
    "USD₮0": 50_000 * 10**6,
    "WBTC": 2 * 10**8,
}


def sel(sig: str) -> str:
    return keccak256(sig.encode()).hex()[:8]


def enc(addr: str) -> str:
    return addr[2:].lower().rjust(64, "0")


def enc_uint(v: int) -> str:
    return f"{v:064x}"


def enc_bool(b: bool) -> str:
    return enc_uint(1 if b else 0)


def enc_bytes4(s4: str) -> str:
    return s4.rjust(64, "0")


def build_calls():
    calls = []
    def add(name, sig, *words):
        calls.append((name, sel(sig), words))

    # Routers (from config/arbitrum.json venues)
    for router in ("0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",   # Uniswap SwapRouter02
                   "0x8A21F6768C1f8075791D08546Dadf6daA0bE820c"):  # Sushi V3
        add(f"setRouter({router[:10]}…)",
            "setRouter(address,bool)", enc(router), enc_bool(True))
    # Swap selectors per target (zero/calldata.py signatures)
    for target, sig in [
        ("0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45", EXACT_INPUT_SINGLE),
        ("0x8A21F6768C1f8075791D08546Dadf6daA0bE820c", EXACT_INPUT_SINGLE_DEADLINE),
    ]:
        add(f"setSelector({sig[:34]}…, {sig})",
            "setSelector(address,bytes4,bool)",
            enc(target), sel(sig).ljust(64, "0"), enc_bool(True))
    # Tokens (live registry)
    reg = build_token_registry(AaveV3(RPC, PROVIDER), BLOCK)
    for sym in TOKENS:
        addr = reg[sym].address
        add(f"setToken({sym})",
            "setToken(address,bool)", enc(addr), enc_bool(True))
    # Caller = hot wallet EOA
    add("setCaller(hot wallet)",
        "setCaller(address,bool)", enc(SENDER), enc_bool(True))
    # Balancer vault (0-premium flash loans)
    add("setBalancerVault",
        "setBalancerVault(address)",
        enc("0xBA12222222228d8Ba445958a75a0704d566BF2C8"))
    # Per-asset max loans
    for sym, amount in MAX_LOAN.items():
        add(f"setMaxLoan({sym})",
            "setMaxLoan(address,uint256)", enc(reg[sym].address), enc_uint(amount))
    return calls


def main():
    rpc = Rpc(RPCS[0])
    nonce = int(rpc.call("eth_getTransactionCount", [SENDER, "pending"]), 16)
    gas_price = int(rpc.call("eth_gasPrice", []), 16) / 1e9 * 2
    sent = []
    for name, s4, words in build_calls():
        data = bytes.fromhex(s4 + "".join(words))
        # Setters are trivial mapping writes (~46k gas worst case); a flat
        # limit avoids flaky per-endpoint estimateGas reverts.
        raw = sign_transaction(KEY, nonce=nonce, gas_price_gwei=gas_price,
                               gas_limit=100_000, to=EXECUTOR,
                               value_wei=0, data=data, chain_id=42161)
        tx_hash = send_raw_transaction(rpc, raw)
        sent.append((name, tx_hash))
        nonce += 1
        time.sleep(0.3)
    print(f"sent {len(sent)} allowlist txs; waiting for receipts…")
    ok = fail = 0
    for name, tx_hash in sent:
        for _ in range(40):
            rec = rpc.call("eth_getTransactionReceipt", [tx_hash])
            if rec:
                status = rec["status"]
                if status == "0x1":
                    ok += 1
                    print(f"  ✓ {name}")
                else:
                    fail += 1
                    print(f"  ✗ FAILED {name} {tx_hash}")
                break
            time.sleep(1.5)
        else:
            fail += 1
            print(f"  ? no receipt {name} {tx_hash}")
    print(f"done: {ok} ok, {fail} failed")


if __name__ == "__main__":
    KEY = bytes.fromhex((KEY_PATH := KEY_PATH).read_text().strip())
    from zero.wallet import private_key_to_address
    SENDER = private_key_to_address(KEY)
    RPC = Rpc(RPCS[0])
    BLOCK = int(RPC.call("eth_blockNumber", []), 16)
    main()