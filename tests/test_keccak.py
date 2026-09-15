import unittest

from zero.keccak import keccak256, selector_hex, topic_of


class TestKeccak(unittest.TestCase):
    def test_known_vectors(self):
        self.assertEqual(
            keccak256(b"").hex(),
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        # keccak256("a") — well-known from Ethereum tooling
        self.assertEqual(
            keccak256(b"a").hex(),
            "3ac225168df54212a25c1c01fd35bebfea408fdac2e31ddd6f80a4bbf9a5f1cb")

    def test_famous_selectors(self):
        # These selector values are fixed by the Solidity ecosystem.
        self.assertEqual(selector_hex("transfer(address,uint256)"), "0xa9059cbb")
        self.assertEqual(selector_hex("transferFrom(address,address,uint256)"),
                         "0x23b872dd")
        self.assertEqual(selector_hex("balanceOf(address)"), "0x70a08231")

    def test_famous_topic(self):
        # ERC-20 Transfer event topic — memorized by every indexer.
        self.assertEqual(topic_of("Transfer(address,address,uint256)").hex(),
                         "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628"
                         "f55a4df523b3ef")

    def test_topic_length(self):
        self.assertEqual(len(topic_of("Transfer(address,address,uint256)")), 32)


if __name__ == "__main__":
    unittest.main()