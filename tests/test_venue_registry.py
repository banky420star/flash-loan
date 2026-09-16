import json
import os
import unittest

from zero.venues.base import PoolRef
from zero.venues.registry import build_venue_registry


CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "arbitrum.json")


class DummyRpc:
    pass


class TestVenueRegistry(unittest.TestCase):
    def config(self):
        with open(CONFIG_PATH) as f:
            return json.load(f)

    def test_enabled_venues_have_unique_ids_and_addresses(self):
        registry = build_venue_registry(self.config(), DummyRpc())
        self.assertEqual(set(registry), {"uniswap_v3", "sushi_v3", "camelot_v3"})
        self.assertEqual(len(registry), len(set(registry)))
        for venue in registry.values():
            self.assertTrue(venue.factory.startswith("0x"))
            self.assertEqual(len(venue.factory), 42)
            self.assertTrue(venue.router.startswith("0x"))
            self.assertEqual(len(venue.router), 42)

    def test_disabled_venue_is_not_registered(self):
        cfg = self.config()
        cfg["venues"]["sushi_v3"]["enabled"] = False
        registry = build_venue_registry(cfg, DummyRpc())
        self.assertNotIn("sushi_v3", registry)

    def test_pool_reference_identity_includes_venue(self):
        common = dict(
            address="0x" + "11" * 20,
            token0="0x" + "22" * 20,
            token1="0x" + "33" * 20,
            fee_tier=500,
        )
        uni = PoolRef(venue_id="uniswap_v3", **common)
        sushi = PoolRef(venue_id="sushi_v3", **common)
        self.assertNotEqual(uni.id, sushi.id)
        self.assertEqual(uni.address, common["address"])

    def test_only_uniswap_has_execution_encoder_initially(self):
        registry = build_venue_registry(self.config(), DummyRpc())
        self.assertTrue(registry["uniswap_v3"].exact_quote_supported)
        self.assertTrue(registry["uniswap_v3"].execution_supported)
        self.assertTrue(registry["sushi_v3"].exact_quote_supported)
        self.assertFalse(registry["sushi_v3"].execution_supported)
        self.assertTrue(registry["camelot_v3"].exact_quote_supported)
        self.assertFalse(registry["camelot_v3"].execution_supported)


if __name__ == "__main__":
    unittest.main()
