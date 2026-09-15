"""Aave V3 adapter: oracle prices, reserve registry, account health.

All contract addresses resolve at runtime from PoolAddressesProvider, so the
adapter never hardcodes a Pool or Oracle address.
"""

from .keccak import keccak256, selector_hex
from .rpc import decode_uints, encode_address, encode_bytes32

# PoolAddressesProvider ids on this deployment: bytes32 of the UTF-8 string
# (left-aligned, zero-padded) — verified live: getAddress(bytes32("POOL"))
# returns the canonical Aave V3 Arbitrum Pool.
def _id(text: str) -> bytes:
    return text.encode().ljust(32, b"\0")


ID_POOL = _id("POOL")
ID_ORACLE = _id("PRICE_ORACLE")

# HF buckets (same ladder the liquidation scanner reports against).
HF_BUCKETS = [
    ("LIQUIDATABLE", 0.0, 1.0),
    ("CRITICAL", 1.0, 1.005),
    ("HOT", 1.005, 1.02),
    ("WARM", 1.02, 1.05),
    ("WATCH", 1.05, 1.10),
    ("SAFE", 1.10, float("inf")),
]


def bucket_for(hf: float) -> str:
    for name, lo, hi in HF_BUCKETS:
        if lo <= hf < hi:
            return name
    return "SAFE"


class AaveV3:
    def __init__(self, rpc, provider_address: str):
        self.rpc = rpc
        self.provider = provider_address

    def _get_address(self, id_bytes: bytes) -> str:
        data = selector_hex("getAddress(bytes32)") + encode_bytes32(id_bytes)[2:]
        raw = self.rpc.eth_call(self.provider, data)
        addr_int = decode_uints(raw)[0]
        return "0x" + addr_int.to_bytes(20, "big").hex()

    def pool_address(self) -> str:
        return self._get_address(ID_POOL)

    def oracle_address(self) -> str:
        return self._get_address(ID_ORACLE)

    def asset_price(self, oracle: str, asset: str) -> float:
        """USD price with 8 decimals, per Aave oracle convention."""
        data = selector_hex("getAssetPrice(address)") + encode_address(asset)[2:]
        raw = self.rpc.eth_call(oracle, data)
        return decode_uints(raw)[0] / 1e8

    # ------------------------------------------------------------ registry
    def reserves_list(self, pool: str) -> list:
        """getReservesList() -> [address]; dynamic abi-encoded array.

        Layout: word0 = offset (bytes), then at that offset: length word,
        then one word per element.
        """
        raw = self.rpc.eth_call(pool, selector_hex("getReservesList()"))
        words = decode_uints(raw)
        start = words[0] // 32
        count = words[start]
        return ["0x" + w.to_bytes(20, "big").hex()
                for w in words[start + 1:start + 1 + count]]

    def symbol(self, token: str) -> str:
        """ERC20 symbol() — dynamic string return."""
        raw = self.rpc.eth_call(token, selector_hex("symbol()"))
        words = decode_uints(raw)
        start = words[0] // 32
        length = words[start]
        chunk = b"".join(w.to_bytes(32, "big") for w in words[start + 1:])
        return chunk[:length].decode()

    def account_data(self, pool: str, user: str) -> dict:
        """getUserAccountData returns 6 uints, all 8-dec USD except HF."""
        data = selector_hex("getUserAccountData(address)") + encode_address(user)[2:]
        words = decode_uints(self.rpc.eth_call(pool, data))
        if len(words) < 6:
            raise ValueError(f"bad getUserAccountData reply ({len(words)} words)")
        collateral_usd, debt_usd, _, threshold_bps, _, hf = words[:6]
        return {
            "collateral_usd": collateral_usd / 1e8,
            "debt_usd": debt_usd / 1e8,
            "liquidation_threshold": threshold_bps / 1e4,
            "health_factor": hf / 1e18,
        }