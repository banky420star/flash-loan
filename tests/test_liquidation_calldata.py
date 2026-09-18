import unittest

from zero.calldata import SWAP_ROUTER_02, SUSHI_V3_ROUTER
from zero.keccak import selector_hex
from zero.liquidation_calldata import build_liquidation_steps

DEBT='0x'+'11'*20
COLL='0x'+'22'*20
BORROWER='0x'+'33'*20
POOL='0x'+'44'*20
DEX_POOL='0x'+'55'*20


def candidate():
    return {
        'route_kind':'liquidation_exact', 'executable':True,
        'base_asset':DEBT, 'collateral_asset':COLL, 'borrower':BORROWER,
        'debt_to_cover_raw':1_000_000_000,
        'collateral_received_raw':522_500_000_000_000_000,
        'unwind_out_raw':1_045_000_000, 'flash_fee_raw':500_000,
        'min_profit_raw':500_001,
        'legs':[{'pool':{
            'venue_id':'uniswap_v3','address':DEX_POOL,
            'token0':DEBT,'token1':COLL,'fee_tier':500,
            'pool_kind':'uniswap_v3'},
            'token_in':COLL,'token_out':DEBT}],
    }


class TestLiquidationCalldata(unittest.TestCase):
    def test_builds_approve_liquidate_approve_swap_steps(self):
        steps=build_liquidation_steps(candidate(), POOL, SWAP_ROUTER_02)
        self.assertEqual(len(steps),4)
        self.assertEqual([s.target.lower() for s in steps],
                         [DEBT.lower(),POOL.lower(),COLL.lower(),SWAP_ROUTER_02.lower()])
        self.assertTrue(steps[0].data.startswith(selector_hex('approve(address,uint256)')))
        self.assertTrue(steps[1].data.startswith(selector_hex(
            'liquidationCall(address,address,address,uint256,bool)')))
        self.assertTrue(steps[2].data.startswith(selector_hex('approve(address,uint256)')))
        self.assertTrue(steps[3].data.startswith(selector_hex(
            'exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))')))

    def test_rejects_unwind_that_cannot_cover_repayment_and_min_profit(self):
        row=candidate(); row['unwind_out_raw']=1_000_000_000
        with self.assertRaises(ValueError):
            build_liquidation_steps(row, POOL, SWAP_ROUTER_02)

    def test_sushi_unwind_targets_sushi_router_with_deadline_struct(self):
        row=candidate(); row['legs'][0]['pool']['venue_id']='sushi_v3'
        steps=build_liquidation_steps(row, POOL, SWAP_ROUTER_02)
        self.assertEqual(len(steps),4)
        self.assertEqual([s.target.lower() for s in steps],
                         [DEBT.lower(),POOL.lower(),COLL.lower(),
                          SUSHI_V3_ROUTER.lower()])
        self.assertTrue(steps[3].data.startswith(selector_hex(
            'exactInputSingle((address,address,uint24,address,'
            'uint256,uint256,uint256,uint160))')))
        # approve of the collateral now targets the Sushi router too
        self.assertTrue(steps[2].data.startswith(selector_hex('approve(address,uint256)')))

    def test_unsupported_venue_still_rejected(self):
        row=candidate(); row['legs'][0]['pool']['venue_id']='camelot_v3'
        with self.assertRaises(ValueError):
            build_liquidation_steps(row, POOL, SWAP_ROUTER_02)


if __name__=='__main__': unittest.main()
