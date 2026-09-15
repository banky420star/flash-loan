"""JSON-RPC client over HTTP with injectable transport (for offline tests)."""

import json
import time
import urllib.request


class RpcError(RuntimeError):
    pass


class UrllibTransport:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout

    def post(self, payload: bytes) -> bytes:
        req = urllib.request.Request(
            self.url, data=payload,
            headers={"Content-Type": "application/json",
                     "User-Agent": "zero-engine/0.2"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()


def _block_tag(block: int | str) -> str:
    if isinstance(block, int):
        if block < 0:
            raise ValueError("block must be non-negative")
        return hex(block)
    if block in {"latest", "pending", "safe", "finalized", "earliest"}:
        return block
    if isinstance(block, str) and block.startswith("0x"):
        return block
    raise ValueError(f"invalid block tag: {block}")


class Rpc:
    def __init__(self, url: str, transport=None, retries: int = 3):
        self.url = url
        self.transport = transport if transport is not None else UrllibTransport(url)
        self.retries = retries
        self._id = 0

    def _request(self, payload: dict) -> dict:
        last_err = None
        for attempt in range(self.retries):
            try:
                raw = self.transport.post(json.dumps(payload).encode())
                resp = json.loads(raw)
                if "error" in resp:
                    raise RpcError(f"RPC error: {resp['error']}")
                return resp
            except RpcError:
                raise
            except Exception as e:  # network flake -> retry with backoff
                last_err = e
                time.sleep(0.3 * (attempt + 1))
        raise RpcError(f"rpc unreachable after {self.retries} attempts: {last_err}")

    def call(self, method: str, params: list):
        self._id += 1
        resp = self._request({"jsonrpc": "2.0", "id": self._id,
                              "method": method, "params": params})
        return resp["result"]

    def batch(self, calls: list) -> list:
        """calls: [(method, params), ...] -> [result, ...] preserving order."""
        payload = []
        for i, (method, params) in enumerate(calls):
            payload.append({"jsonrpc": "2.0", "id": i,
                            "method": method, "params": params})
        raw = self.transport.post(json.dumps(payload).encode())
        results = {r["id"]: r for r in json.loads(raw)}
        out = []
        for i in range(len(calls)):
            r = results[i]
            if "error" in r:
                raise RpcError(f"RPC error: {r['error']}")
            out.append(r["result"])
        return out

    def batch_eth_call(self, calls: list[tuple[str, str]], *,
                       block: int | str = "latest",
                       max_batch: int = 100) -> list[bytes]:
        """Run ordered eth_call requests in bounded batches at one block tag.

        There is intentionally no per-call fallback. If any batch member fails,
        the entire helper raises so a pinned-block scan cannot silently mix
        state from another block.
        """
        max_batch = int(max_batch)
        if max_batch <= 0:
            raise ValueError("max_batch must be positive")
        tag = _block_tag(block)
        out: list[bytes] = []
        for start in range(0, len(calls), max_batch):
            chunk = calls[start:start + max_batch]
            raw_results = self.batch([
                ("eth_call", [{"to": to, "data": data}, tag])
                for to, data in chunk
            ])
            for raw in raw_results:
                if raw == "0x" or raw is None:
                    out.append(b"")
                else:
                    out.append(bytes.fromhex(raw[2:]))
        return out

    # convenience wrappers -------------------------------------------------
    def chain_id(self) -> int:
        return int(self.call("eth_chainId", []), 16)

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

    def get_code(self, address: str, block: int | str = "latest") -> str:
        return self.call("eth_getCode", [address, _block_tag(block)])

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> bytes:
        raw = self.call("eth_call", [{"to": to, "data": data}, _block_tag(block)])
        if raw == "0x" or raw is None:
            return b""
        return bytes.fromhex(raw[2:])

    def gas_price(self) -> int:
        return int(self.call("eth_gasPrice", []), 16)


# ---------------------------------------------------------------- decoding
def decode_uints(data: bytes) -> list:
    """Decode abi-encoded uint256[] static args (word-aligned, no offsets)."""
    if len(data) % 32 != 0:
        raise ValueError(f"not word-aligned: {len(data)} bytes")
    return [int.from_bytes(data[i:i + 32], "big") for i in range(0, len(data), 32)]


def encode_uint(v: int) -> str:
    return "0x" + v.to_bytes(32, "big").hex()


def encode_address(addr: str) -> str:
    return "0x" + "0" * 24 + addr.lower().removeprefix("0x")


def encode_bytes32(b: bytes) -> str:
    return "0x" + b.hex()


def to_int(h: str) -> int:
    return int(h, 16)


def to_checksum(addr: str) -> str:
    """EIP-55 checksum address (keccak-based)."""
    from .keccak import keccak256
    a = addr.lower().removeprefix("0x")
    h = keccak256(a.encode()).hex()
    out = "0x"
    for i, ch in enumerate(a):
        out += ch.upper() if ch in "abcdef" and int(h[i], 16) >= 8 else ch
    return out
