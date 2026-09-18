#!/usr/bin/env python3
"""Generate the hot wallet for live execution.

Writes the private key to wallet/hot.key (0600, gitignored) and prints the
address plus funding instructions. Refuses to overwrite an existing key —
a regenerate means a new address; delete the file explicitly if you mean it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zero.wallet import generate_private_key, private_key_to_address

ROOT = Path(__file__).resolve().parent.parent
KEY_PATH = ROOT / "wallet" / "hot.key"


def main() -> None:
    if KEY_PATH.exists():
        key = bytes.fromhex(KEY_PATH.read_text().strip())
        print(f"existing wallet found: {private_key_to_address(key)}")
        return
    KEY_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    KEY_PATH.write_bytes(generate_private_key())
    os.chmod(KEY_PATH, 0o600)
    address = private_key_to_address(KEY_PATH.read_bytes())
    print(f"new hot wallet: {address}")
    print("fund it with gas money, e.g.:")
    print("  0.02-0.05 ETH on Arbitrum One (gas for deploy + failed attempts)")
    print("the key never leaves this machine; wallet/ is gitignored")


if __name__ == "__main__":
    main()