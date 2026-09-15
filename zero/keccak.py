"""Pure-Python keccak-256 (the Ethereum variant, padding 0x01).

Ethereum's function selectors are the first 4 bytes of keccak256 of the
canonical signature. hashlib's sha3_256 is a *different* function (padding
0x06), so we implement keccak ourselves to stay dependency-free.

Correctness is pinned by test vectors in tests/test_keccak.py.
"""

_MASK = (1 << 64) - 1

# Round constants for the iota step (24 rounds).
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]

# Rho rotation offsets, generated from the Keccak lane tour:
# start at (1,0), offset ((t+1)(t+2))/2 mod 64, step (x,y) -> (y, (2x+3y)%5).
def _gen_rho():
    rho = [[0] * 5 for _ in range(5)]
    x, y = 1, 0
    for t in range(24):
        rho[x][y] = ((t + 1) * (t + 2) // 2) % 64
        x, y = y, (2 * x + 3 * y) % 5
    return rho


_RHO = _gen_rho()

assert sum(len(row) for row in _RHO) == 25
assert len(_RC) == 24


def _rotl(v: int, n: int) -> int:
    n %= 64
    return ((v << n) | (v >> (64 - n))) & _MASK


def _keccak_f(state):
    """state: 5x5 list of 64-bit lanes, A[x][y]."""
    for rnd in range(24):
        # theta
        c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4]
             for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x][y] ^= d[x]
        # rho + pi
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(state[x][y], _RHO[x][y])
        # chi
        for x in range(5):
            for y in range(5):
                state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) & _MASK
        # iota
        state[0][0] ^= _RC[rnd]
    return state


def _pad10_1(msg: bytes, rate: int) -> bytes:
    pad_len = rate - (len(msg) % rate)
    if pad_len == 1:
        return msg + b"\x81"
    return msg + b"\x01" + b"\x00" * (pad_len - 2) + b"\x80"


def keccak256(data: bytes) -> bytes:
    rate = 136  # bytes, for 256-bit output
    state = [[0] * 5 for _ in range(5)]
    padded = _pad10_1(data, rate)

    for block_start in range(0, len(padded), rate):
        block = padded[block_start:block_start + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
            x, y = i % 5, i // 5
            state[x][y] ^= lane
        _keccak_f(state)

    out = bytearray()
    for i in range(4):  # 32 bytes = 4 lanes
        x, y = i % 5, i // 5
        out += state[x][y].to_bytes(8, "little")
    return bytes(out)


def selector(signature: str) -> bytes:
    """First 4 bytes of keccak256 of the canonical signature string."""
    return keccak256(signature.encode())[:4]


def selector_hex(signature: str) -> str:
    return "0x" + selector(signature).hex()


def topic_of(signature: str) -> bytes:
    """Full 32-byte topic for event signatures."""
    return keccak256(signature.encode())