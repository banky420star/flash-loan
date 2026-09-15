"""Uniswap V3 pool adapter: exact single-range swap math + on-chain state.

Within one tick range the pool is a constant-L virtual AMM:
    x_res = L / sqrtP,  y_res = L * sqrtP   (sqrtP = sqrtPriceX96 / 2**96)

Swapping token0 in moves sqrtP DOWN; token1 in moves it UP. We compute the
exact post-swap sqrtP with rational arithmetic, so quotes carry no float
error as long as the swap does not cross a tick boundary.
"""

from fractions import Fraction
from .keccak import selector_hex
from .rpc import decode_uints


def _sel(sig: str) -> str:
    return selector_hex(sig)


class UniswapV3Pool:
    def __init__(self, rpc, pool_address: str, token0: str, token1: str,
                 fee_percent: float, decimals0: int, decimals1: int):
        self.rpc = rpc
        self.address = pool_address
        self.token0 = token0
        self.token1 = token1
        self.fee_percent = fee_percent  # 0.05 means 0.05%
        self.decimals0 = decimals0
        self.decimals1 = decimals1

    def fetch_state(self, block: int | str = "latest") -> dict:
        slot0 = self.rpc.eth_call(self.address, _sel("slot0()"), block=block)
        liq = self.rpc.eth_call(self.address, _sel("liquidity()"), block=block)
        words = decode_uints(slot0) if slot0 else []
        return {
            "sqrtPriceX96": words[0] if words else 0,
            "liquidity": decode_uints(liq)[0] if liq else 0,
        }

    @staticmethod
    def quote(state: dict, amount_in: float, direction: str,
              decimals_in: int, decimals_out: int, fee_percent: float,
              max_price_move: float = 0.02) -> dict:
        """Exact single-range quote. direction: '0to1' or '1to0'.

        fee_percent: pool fee as percent (0.05 = 0.05%).
        Returns {"out": float, "out_of_range": bool}. out_of_range means the
        implied price move exceeds max_price_move (tick crossing likely) and
        the quote must not be trusted.
        """
        sqrt_x96 = state["sqrtPriceX96"]
        liquidity = state["liquidity"]
        if sqrt_x96 <= 0 or liquidity <= 0:
            return {"out": 0.0, "out_of_range": True}

        n = Fraction(sqrt_x96)
        l = Fraction(liquidity)
        two96 = Fraction(1 << 96)

        amt = Fraction(amount_in).limit_denominator(10 ** 24)
        amt_in = amt * (1 - Fraction(str(fee_percent)) / 100)

        price_before = Fraction(n, two96)

        if direction == "0to1":
            amt_units = amt_in * (10 ** decimals_in)
            one_over_new = Fraction(1) / n + amt_units / (l * two96)
            n_new = Fraction(1) / one_over_new
            out_units = l * (price_before - n_new / two96)
        elif direction == "1to0":
            amt_units = amt_in * (10 ** decimals_in)
            n_new = n + amt_units * two96 / l
            out_units = l * (two96 / n - two96 / n_new)
        else:
            raise ValueError(f"bad direction: {direction}")

        out = float(out_units / (10 ** decimals_out))

        moved = abs(n_new / two96 - price_before) / price_before
        return {"out": out, "out_of_range": moved > Fraction(str(max_price_move))}

    @staticmethod
    def spot_price_raw(state: dict) -> Fraction:
        """Marginal price in RAW units (token1_raw per token0_raw)."""
        n = Fraction(state["sqrtPriceX96"])
        return (n / (1 << 96)) ** 2