import unittest

from scripts.swarm_live_smoke import configure_marketdata_smoke


class TestSwarmLiveSmokeScope(unittest.TestCase):
    def test_live_smoke_scopes_to_batched_uniswap_marketdata_path(self):
        cfg = {
            "swarm": {"multihop_enabled": True,
                      "verify_positive_candidates": True},
            "venues": {
                "uniswap_v3": {"enabled": True},
                "sushi_v3": {"enabled": True},
                "camelot_v3": {"enabled": True},
            },
        }
        configure_marketdata_smoke(cfg)
        self.assertFalse(cfg["swarm"]["verify_positive_candidates"])
        self.assertFalse(cfg["swarm"]["multihop_enabled"])
        self.assertTrue(cfg["venues"]["uniswap_v3"]["enabled"])
        self.assertFalse(cfg["venues"]["sushi_v3"]["enabled"])
        self.assertFalse(cfg["venues"]["camelot_v3"]["enabled"])


if __name__ == "__main__":
    unittest.main()
