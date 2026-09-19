import unittest

from zero.keccak import selector_hex
from zero.swarm import TokenInfo
from zero.swarm_batch import build_scan_context_batched


AAVE_POOL = "0x" + "01" * 20
ORACLE = "0x" + "02" * 20
WETH = "0x" + "03" * 20
POOL_A = "0x" + "aa" * 20
POOL_B = "0x" + "bb" * 20


def abi_uint(value: int) -> bytes:
    return value.to_bytes(32, "big")


ROUTES = [
    {
        "block": 555,
        "pools": [
            {"address": POOL_A},
            {"address": POOL_B},
        ],
    },
    {
        "block": 555,
        "pools": [
            {"address": POOL_B},
            {"address": POOL_A},
        ],
    },
]


class FakeAave:
    def __init__(self):
        self.calls = []

    def pool_address(self, block="latest"):
        self.calls.append(("pool", block))
        return AAVE_POOL

    def oracle_address(self, block="latest"):
        self.calls.append(("oracle", block))
        return ORACLE

    def flashloan_premium_total(self, pool, block="latest"):
        self.calls.append(("premium", pool, block))
        return 5

    def asset_price(self, oracle, asset, block="latest"):
        self.calls.append(("price", oracle, asset, block))
        return 2400.0


class FakeRpc:
    def __init__(self, *, fail=False):
        self.batches = []
        self.fail = fail

    def batch_eth_call(self, calls, *, block="latest", max_batch=100):
        self.batches.append((list(calls), block, max_batch))
        if self.fail:
            raise RuntimeError("batch failed")
        replies = []
        for target, data in calls:
            if data == selector_hex("slot0()"):
                replies.append(abi_uint(111 if target.lower() == POOL_A.lower() else 222))
            elif data == selector_hex("liquidity()"):
                replies.append(abi_uint(1000 if target.lower() == POOL_A.lower() else 2000))
            else:
                raise AssertionError(f"unexpected call {target} {data}")
        return replies


class FakeEngine:
    def __init__(self, *, fail=False):
        self.aave = FakeAave()
        self.rpc = FakeRpc(fail=fail)
        self.config = {"arbitrage": {"eth_for_gas": WETH}}

    def _gas_cost_usd(self, eth_price):
        return eth_price / 1000.0


TOKENS = {
    "WETH": TokenInfo("WETH", WETH, 18, 2400.0),
}


class TestBatchedScanContext(unittest.TestCase):
    def test_unique_pool_state_is_batched_once_at_pinned_block(self):
        engine = FakeEngine()
        context = build_scan_context_batched(
            engine, 555, ROUTES, tokens=TOKENS,
            aave_pool=AAVE_POOL, oracle=ORACLE, max_batch=100)

        self.assertEqual(len(engine.rpc.batches), 1)
        calls, block, max_batch = engine.rpc.batches[0]
        self.assertEqual(block, 555)
        self.assertEqual(max_batch, 100)
        self.assertEqual(len(calls), 4)
        self.assertEqual(sum(1 for _, data in calls if data == selector_hex("slot0()")), 2)
        self.assertEqual(sum(1 for _, data in calls if data == selector_hex("liquidity()")), 2)

        self.assertEqual(context.block, 555)
        self.assertEqual(context.aave_pool, AAVE_POOL)
        self.assertEqual(context.oracle, ORACLE)
        # Scan models the executor's live path: runBalancer, 0% premium.
        self.assertEqual(context.premium_bps, 0)
        self.assertAlmostEqual(context.eth_price_usd, 2400.0)
        self.assertAlmostEqual(context.gas_usd, 2.4)
        self.assertEqual(context.pool_states[POOL_A]["sqrtPriceX96"], 111)
        self.assertEqual(context.pool_states[POOL_A]["liquidity"], 1000)
        self.assertEqual(context.pool_states[POOL_B]["sqrtPriceX96"], 222)
        self.assertEqual(context.pool_states[POOL_B]["liquidity"], 2000)
        self.assertNotIn(("price", ORACLE, WETH, 555), engine.aave.calls)

    def test_batch_failure_propagates_without_latest_fallback(self):
        engine = FakeEngine(fail=True)
        with self.assertRaisesRegex(RuntimeError, "batch failed"):
            build_scan_context_batched(
                engine, 555, ROUTES, tokens=TOKENS,
                aave_pool=AAVE_POOL, oracle=ORACLE, max_batch=100)
        self.assertEqual(len(engine.rpc.batches), 1)
        self.assertEqual(engine.rpc.batches[0][1], 555)

    def test_mixed_block_catalog_is_rejected_before_rpc_batch(self):
        engine = FakeEngine()
        bad = [dict(ROUTES[0]), dict(ROUTES[1])]
        bad[1]["block"] = 554
        with self.assertRaisesRegex(ValueError, "mixed-block scan context"):
            build_scan_context_batched(
                engine, 555, bad, tokens=TOKENS,
                aave_pool=AAVE_POOL, oracle=ORACLE, max_batch=100)
        self.assertEqual(engine.rpc.batches, [])


if __name__ == "__main__":
    unittest.main()
