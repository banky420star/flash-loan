import unittest

from zero.wallet import (
    _GX,
    _GY,
    _N,
    _P,
    _point_add,
    _point_mul,
    _inv,
    generate_private_key,
    private_key_to_address,
    recover_signer,
    sign_transaction,
    _rlp_encode,
)


def _decode_legacy_tx(raw_hex: str, chain_id: int):
    """Parse a signed legacy EIP-155 payload back into its fields."""
    payload = bytes.fromhex(raw_hex[2:])
    if payload[0] < 0xF8:
        body = payload[1:1 + payload[0] - 0xC0]
    else:
        len_of_len = payload[0] - 0xF7
        length = int.from_bytes(payload[1:1 + len_of_len], "big")
        body = payload[1 + len_of_len:1 + len_of_len + length]

    items = []
    i = 0
    while i < len(body):
        b = body[i]
        if b < 0x80:            # single byte
            items.append(b)
            i += 1
        elif b < 0xB8:          # short string
            l = b - 0x80
            items.append(int.from_bytes(body[i + 1:i + 1 + l], "big"))
            i += 1 + l
        elif b < 0xC0:          # long string
            ll = b - 0xB7
            l = int.from_bytes(body[i + 1:i + 1 + ll], "big")
            items.append(int.from_bytes(body[i + 1 + ll:i + 1 + ll + l], "big"))
            i += 1 + ll + l
        else:
            raise AssertionError("nested list in flat tx payload")
    assert len(items) == 9, items
    nonce, gas_price, gas_limit, _to, value, _data, v, r, s = items
    y_parity = v - 35 - 2 * chain_id
    assert y_parity in (0, 1), v
    unsigned = _rlp_encode([nonce, gas_price, gas_limit, _to, value, _data,
                            chain_id, 0, 0])
    return unsigned, y_parity, r, s


class TestWallet(unittest.TestCase):
    def test_known_answer_address(self):
        # Canonical vector: private key 1 -> the well-known first address.
        key = bytes.fromhex("00" * 31 + "01")
        self.assertEqual(private_key_to_address(key).lower(),
                         "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf")

    def test_curve_constants_are_real(self):
        # Group-order identities: N*G must be the point at infinity and
        # (N-1)*G must be -G. A hallucinated order constant fails both.
        self.assertIsNone(_point_mul(_N, (_GX, _GY)))
        self.assertEqual(_point_mul(_N - 1, (_GX, _GY)),
                         (_GX, (_P - _GY) % _P))

    def test_sign_and_recover_roundtrip(self):
        for _ in range(4):
            key = generate_private_key()
            address = private_key_to_address(key)
            raw = sign_transaction(key, nonce=7, gas_price_gwei=0.01,
                                   gas_limit=300_000,
                                   to="0x" + "11" * 20, value_wei=0,
                                   data=bytes.fromhex("1234"),
                                   chain_id=42161)
            unsigned, y_parity, r, s = _decode_legacy_tx(raw, 42161)
            # Canonical signature: s must be in the lower half of the order.
            self.assertLess(s, _N // 2)
            self.assertEqual(recover_signer(unsigned, y_parity, r, s),
                             address)

    def test_signature_verifies_against_independent_check(self):
        # Re-derive the signer's public key straight from the verification
        # equation (u2*R + u1*G) and compare with d*G — this does not trust
        # recover_signer's own bookkeeping.
        from zero.keccak import keccak256
        key = generate_private_key()
        expected_pub = _point_mul(int.from_bytes(key, "big"), (_GX, _GY))
        raw = sign_transaction(key, nonce=0, gas_price_gwei=0.1,
                               gas_limit=21000, to="0x" + "22" * 20,
                               value_wei=1, data=b"", chain_id=42161)
        unsigned, y_parity, r, s = _decode_legacy_tx(raw, 42161)
        z = int.from_bytes(keccak256(unsigned), "big")
        y = pow((r * r % _P * r + 7) % _P, (_P + 1) // 4, _P)
        if (y ^ y_parity) & 1:
            y = _P - y
        point_r = (r, y)
        r_inv = _inv(r, _N)
        pub = _point_add(_point_mul((s * r_inv) % _N, point_r),
                         _point_mul((-z * r_inv) % _N, (_GX, _GY)))
        self.assertEqual(pub, expected_pub)

    def test_wrong_key_does_not_recover(self):
        key_a = generate_private_key()
        key_b = generate_private_key()
        addr_a = private_key_to_address(key_a)
        addr_b = private_key_to_address(key_b)
        self.assertNotEqual(addr_a, addr_b)
        raw = sign_transaction(key_a, nonce=1, gas_price_gwei=0.01,
                               gas_limit=21000, to="0x" + "33" * 20,
                               value_wei=0, data=b"", chain_id=42161)
        unsigned, y_parity, r, s = _decode_legacy_tx(raw, 42161)
        self.assertNotEqual(recover_signer(unsigned, y_parity, r, s), addr_b)

    def test_key_generation_bounds(self):
        for _ in range(16):
            key = generate_private_key()
            self.assertEqual(len(key), 32)
            self.assertTrue(0 < int.from_bytes(key, "big") < _N)


if __name__ == "__main__":
    unittest.main()