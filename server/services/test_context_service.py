import unittest

from .context_service import ContextExpired, ContextService, IdempotencyConflict


class ContextServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.service = ContextService("x" * 32, clock=lambda: self.now[0], token_ttl=300)

    def _context(self, **overrides):
        values = dict(
            dataset_id="ds1", dataset_version="v1", pool_version="p1", pool_hash="h1",
            pool_count=2, rule_version="r1", time_context_id="tc1", target_date="20261029",
            role="current", query={"limit": 20}, next_switch_at=2000, stale_until=1500,
        )
        values.update(overrides)
        return self.service.build_context(**values)

    def test_expiry_uses_earliest_boundary(self):
        context, token = self._context()
        self.assertEqual(context.expires_at, 1300)
        self.assertEqual(self.service.verify(token), context)

    def test_token_tamper_and_expiry_are_rejected(self):
        _context, token = self._context()
        with self.assertRaises(ContextExpired):
            self.service.verify(token[:-1] + ("a" if token[-1] != "a" else "b"))
        self.now[0] = 1300
        with self.assertRaises(ContextExpired):
            self.service.verify(token)

    def test_entry_id_replay_is_idempotent_and_conflict_is_rejected(self):
        context, _token = self._context()
        first, first_token = self.service.idempotent_context("entry-1", {"page": "prediction"}, context)
        second, second_token = self.service.idempotent_context("entry-1", {"page": "prediction"}, context)
        self.assertEqual(first, second)
        self.assertEqual(first_token, second_token)
        with self.assertRaises(IdempotencyConflict):
            self.service.idempotent_context("entry-1", {"page": "stock"}, context)


if __name__ == "__main__":
    unittest.main()
