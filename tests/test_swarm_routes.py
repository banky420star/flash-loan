import unittest


class TestSwarmRoutes(unittest.TestCase):
    def _types(self):
        try:
            from zero.swarm import RouteKey, RouteLeaseRegistry
        except (ImportError, ModuleNotFoundError) as exc:
            self.fail(f"swarm route primitives are missing: {exc}")
        return RouteKey, RouteLeaseRegistry

    def _route(self, RouteKey, **overrides):
        values = dict(
            chain_id=42161,
            base="0x" + "AA" * 20,
            quote="0x" + "bb" * 20,
            pool_a="0x" + "CC" * 20,
            pool_b="0x" + "dd" * 20,
            fee_a=500,
            fee_b=3000,
            direction="base-to-quote-to-base",
        )
        values.update(overrides)
        return RouteKey(**values)

    def test_route_id_canonicalizes_addresses(self):
        RouteKey, _ = self._types()
        mixed = self._route(RouteKey)
        lower = self._route(
            RouteKey,
            base=("0x" + "AA" * 20).lower(),
            quote=("0x" + "bb" * 20).lower(),
            pool_a=("0x" + "CC" * 20).lower(),
            pool_b=("0x" + "dd" * 20).lower(),
        )
        self.assertEqual(mixed.id, lower.id)
        self.assertEqual(len(mixed.id), 66)
        self.assertTrue(mixed.id.startswith("0x"))

    def test_fee_and_direction_are_part_of_identity(self):
        RouteKey, _ = self._types()
        original = self._route(RouteKey)
        fee_changed = self._route(RouteKey, fee_b=10000)
        direction_changed = self._route(RouteKey, direction="reverse")
        self.assertNotEqual(original.id, fee_changed.id)
        self.assertNotEqual(original.id, direction_changed.id)

    def test_one_owner_per_route_per_block(self):
        RouteKey, RouteLeaseRegistry = self._types()
        route = self._route(RouteKey)
        leases = RouteLeaseRegistry()
        self.assertTrue(leases.claim(100, route.id, "A1"))
        self.assertFalse(leases.claim(100, route.id, "A2"))
        self.assertTrue(leases.claim(101, route.id, "A2"))

    def test_release_requires_owner_and_releases_route(self):
        RouteKey, RouteLeaseRegistry = self._types()
        route = self._route(RouteKey)
        leases = RouteLeaseRegistry()
        self.assertTrue(leases.claim(100, route.id, "A1"))
        with self.assertRaises(ValueError):
            leases.release(100, route.id, "A2")
        leases.release(100, route.id, "A1")
        self.assertTrue(leases.claim(100, route.id, "A2"))

    def test_expire_before_drops_old_block_leases_only(self):
        RouteKey, RouteLeaseRegistry = self._types()
        r1 = self._route(RouteKey)
        r2 = self._route(RouteKey, pool_b="0x" + "ee" * 20)
        leases = RouteLeaseRegistry()
        self.assertTrue(leases.claim(100, r1.id, "A1"))
        self.assertTrue(leases.claim(101, r2.id, "A2"))
        leases.expire_before(101)
        self.assertTrue(leases.claim(100, r1.id, "A3"))
        self.assertFalse(leases.claim(101, r2.id, "A3"))


if __name__ == "__main__":
    unittest.main()
