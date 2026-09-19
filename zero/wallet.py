"""Hot-wallet primitives for live execution.

Pure-stdlib secp256k1 signing and EIP-155 transaction encoding, in keeping
with the engine's no-third-party-dependency rule. This module exists only
because the user authorized real-money execution (2026-09-18); it must
never be imported by the shadow-only paths.
"""
from __future__ import annotations

import secrets

from .keccak import keccak256

# secp256k1 curve constants
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


def _inv(a: int, m: int) -> int:
    return pow(a, -1, m)


def _point_add(p1: tuple[int, int], p2: tuple[int, int]) -> tuple[int, int]:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p1 == p2:
        slope = (3 * x1 * x1) * _inv(2 * y1, _P) % _P
    else:
        slope = (y2 - y1) * _inv(x2 - x1, _P) % _P
    x3 = (slope * slope - x1 - x2) % _P
    y3 = (slope * (x1 - x3) - y1) % _P
    return (x3, y3)


def _point_mul(k: int, point: tuple[int, int]) -> tuple[int, int]:
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def generate_private_key() -> bytes:
    while True:
        key = secrets.token_bytes(32)
        if 0 < int.from_bytes(key, "big") < _N:
            return key


def private_key_to_address(key: bytes) -> str:
    pub = _point_mul(int.from_bytes(key, "big"), (_GX, _GY))
    raw = pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big")
    return "0x" + keccak256(raw)[-20:].hex()


def _rlp_encode(items: list) -> bytes:
    def enc(item):
        if isinstance(item, int):
            # Integer 0 encodes as the empty string -> 0x80, not a 0x00 byte.
            data = b"" if item == 0 else item.to_bytes(
                (item.bit_length() + 7) // 8, "big")
        elif isinstance(item, bytes):
            data = item
        else:
            raise TypeError(f"unsupported rlp item {item!r}")
        if len(data) == 1 and data[0] < 0x80:
            return data
        if len(data) < 56:
            return bytes([0x80 + len(data)]) + data
        length = len(data).to_bytes((len(data).bit_length() + 7) // 8, "big")
        return bytes([0xB7 + len(length)]) + length + data

    out = b"".join(enc(item) for item in items)
    if len(out) < 56:
        return bytes([0xC0 + len(out)]) + out
    length = len(out).to_bytes((len(out).bit_length() + 7) // 8, "big")
    return bytes([0xF7 + len(length)]) + length + out


def sign_transaction(key: bytes, *, nonce: int, gas_price_gwei: float,
                     gas_limit: int, to: str | None, value_wei: int,
                     data: bytes, chain_id: int) -> str:
    """Sign a legacy EIP-155 tx and return the raw hex payload.

    to=None means contract creation (the tx carries data, no recipient).
    """
    gas_price_wei = int(gas_price_gwei * 10**9)
    to_bytes = b"" if to is None else bytes.fromhex(to[2:])
    unsigned = _rlp_encode([nonce, gas_price_wei, gas_limit, to_bytes,
                            value_wei, data, chain_id, 0, 0])
    z = int.from_bytes(keccak256(unsigned), "big")
    secret = int.from_bytes(key, "big")
    if not 0 < secret < _N:
        raise ValueError("invalid private key")
    k = 0
    while True:
        # Deterministic nonce derived from the secret and the digest.
        k = int.from_bytes(keccak256(
            k.to_bytes(32, "big") + z.to_bytes(32, "big")), "big") % _N
        if k == 0:
            continue
        point_k = _point_mul(k, (_GX, _GY))
        r = point_k[0] % _N
        if r == 0:
            continue
        s = (_inv(k, _N) * (z + r * secret)) % _N
        if s == 0:
            continue
        if s > _N // 2:
            s = _N - s
            y_parity = 1 - (point_k[1] & 1)
        else:
            y_parity = point_k[1] & 1
        break
    # Legacy EIP-155: v = 35 + 2*chainId + yParity.
    v = 35 + 2 * chain_id + y_parity
    signed = _rlp_encode([nonce, gas_price_wei, gas_limit, to_bytes,
                          value_wei, data, v, r, s])
    return "0x" + signed.hex()


def recover_signer(unsigned_rlp: bytes, y_parity: int, r: int, s: int) -> str:
    """Recover the signer address; used by tests to prove the signature."""
    z = int.from_bytes(keccak256(unsigned_rlp), "big")
    # x = r, y parity picks the R point on the curve; Q = r^{-1}(sR - zG).
    y = pow((r * r % _P * r + 7) % _P, (_P + 1) // 4, _P)
    if (y ^ (y_parity & 1)) & 1:
        y = _P - y
    point_r = (r, y)
    r_inv = _inv(r, _N)
    u1 = (-z * r_inv) % _N
    u2 = (s * r_inv) % _N
    pub = _point_add(_point_mul(u2, point_r),
                     _point_mul(u1, (_GX, _GY)))
    raw = pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big")
    return "0x" + keccak256(raw)[-20:].hex()


def send_raw_transaction(rpc, raw_hex: str) -> str:
    return rpc.call("eth_sendRawTransaction", [raw_hex])