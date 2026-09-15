import time
import unittest


class TestSwarmOpportunityBook(unittest.TestCase):
    def _types(self):
        try:
            from zero.swarm import OpportunityBook, SwarmCandidate, swarm_expected_net
        except ImportError as exc:
            self.fail(f"swarm opportunity primitives are missing: {exc}")
        return OpportunityBook, SwarmCandidate, swarm_expected_net

    def _candidate(self, SwarmCandidate, *, route_id="route-1", expected_net=0.01,
                   candidate_id="cand-1"):
        return SwarmCandidate(
            candidate_id=candidate_id,
            route_id=route_id,
            worker_id="A1",
            manager_id="ALPHA",
            block=123,
            loan_size=1000.0,
            gross_profit=1.0,
            flash_fee=0.4,
            gas_cost=0.5,
            model_reserve=0.09,
            expected_net=expected_net,
            roi=expected_net / 1000.0,
            timestamp=time.time(),
            payload={"candidate": {"block": 123}},
        )

    def test_decimal_economics_preserve_tiny_positive_boundary(self):
        _, _, swarm_expected_net = self._types()
        self.assertEqual(swarm_expected_net(1.00, 0.40, 0.50, 0.09), 0.01)
        self.assertEqual(swarm_expected_net(1.00, 0.40, 0.50, 0.10), 0.0)

    def test_book_accepts_tiny_positive_and_rejects_zero_or_negative(self):
        OpportunityBook, SwarmCandidate, _ = self._types()
        book = OpportunityBook()
        self.assertTrue(book.add(self._candidate(SwarmCandidate, expected_net=0.000001)))
        self.assertFalse(book.add(self._candidate(
            SwarmCandidate, route_id="route-2", candidate_id="cand-2", expected_net=0.0)))
        self.assertFalse(book.add(self._candidate(
            SwarmCandidate, route_id="route-3", candidate_id="cand-3", expected_net=-0.01)))
        self.assertEqual(len(book.ranked()), 1)

    def test_book_suppresses_duplicate_route_for_same_block(self):
        OpportunityBook, SwarmCandidate, _ = self._types()
        book = OpportunityBook()
        self.assertTrue(book.add(self._candidate(SwarmCandidate)))
        self.assertFalse(book.add(self._candidate(
            SwarmCandidate, candidate_id="cand-duplicate", expected_net=1.5)))
        self.assertEqual(len(book.ranked()), 1)

    def test_ranking_is_expected_net_descending(self):
        OpportunityBook, SwarmCandidate, _ = self._types()
        book = OpportunityBook()
        book.add(self._candidate(
            SwarmCandidate, route_id="r-low", candidate_id="c-low", expected_net=0.02))
        book.add(self._candidate(
            SwarmCandidate, route_id="r-high", candidate_id="c-high", expected_net=2.0))
        book.add(self._candidate(
            SwarmCandidate, route_id="r-mid", candidate_id="c-mid", expected_net=0.4))
        self.assertEqual(
            [c.candidate_id for c in book.ranked()],
            ["c-high", "c-mid", "c-low"],
        )

    def test_candidate_serializes_payload_and_economics(self):
        _, SwarmCandidate, _ = self._types()
        row = self._candidate(SwarmCandidate).as_dict()
        self.assertEqual(row["route_id"], "route-1")
        self.assertEqual(row["expected_net"], 0.01)
        self.assertEqual(row["payload"]["candidate"]["block"], 123)


if __name__ == "__main__":
    unittest.main()
