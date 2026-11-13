"""Executable Redis/Lua adapter checks against an isolated local simulator.

Install server/requirements-test.txt in a test-only environment; no cloud
Redis address or application credentials are loaded.
"""
import unittest
from unittest.mock import patch
from .coordinator import RealtimeCoordinator, build_realtime_key, RealtimeLease

try:
    import fakeredis
except ImportError:
    fakeredis = None


@unittest.skipIf(fakeredis is None, "install server/requirements-test.txt for isolated Redis/Lua checks")
class QuotePipelineTests(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.first = RealtimeCoordinator()
        self.second = RealtimeCoordinator()
        self.first._redis = self.redis
        self.second._redis = self.redis
        self.a = build_realtime_key('singleflight', 'A', 'policy', 'bucket')
        self.b = build_realtime_key('singleflight', 'B', 'policy', 'bucket')

    def test_quote_mget_and_lease_lua_keys_share_required_cluster_slots(self):
        from redis.crc import key_slot
        quotes = [build_realtime_key('quote', code, 'policy', 'bucket') for code in ('A', 'B', 'INDEX')]
        self.assertEqual(len({key_slot(key.encode()) for key in quotes}), 1)
        self.assertEqual(key_slot((self.a + ':lease').encode()), key_slot((self.a + ':lease:attempts').encode()))

    def test_intersections_and_exception_release_are_owner_safe(self):
        with self.first.quote_leases([self.a]) as owned:
            self.assertEqual(owned, {self.a})
            with self.second.quote_leases([self.a, self.b]) as joined:
                self.assertEqual(joined, {self.b})
            self.assertIsNotNone(self.redis.get(self.a + ':lease'))
        self.assertFalse(self.redis.exists(self.a + ':lease'))
        with self.assertRaises(ValueError):
            with self.first.quote_leases([self.a]):
                raise ValueError('source error')
        with self.second.quote_leases([self.a]) as owned:
            self.assertEqual(owned, {self.a})

    def test_replaced_owner_cannot_remove_successor_and_takeover_cap_is_two(self):
        with self.first.quote_leases([self.a]):
            self.redis.delete(self.a + ':lease')  # simulate lease expiry
            with self.second.quote_leases([self.a]) as owned:
                self.assertEqual(owned, {self.a})
                successor = self.redis.get(self.a + ':lease')
                stale = RealtimeLease(self.a, 'expired-owner', 0, 0)
                self.assertFalse(self.first.release(stale))
                self.assertEqual(self.redis.get(self.a + ':lease'), successor)
        self.redis.set(self.a + ':lease:attempts', 2, ex=20)
        with self.first.quote_leases([self.a]) as owned:
            self.assertEqual(owned, set())
        self.assertFalse(self.redis.exists(self.a + ':lease'))

    def test_stale_batch_finalizer_keeps_successor_lease_and_attempts(self):
        first = self.first.quote_leases([self.a])
        first.__enter__()
        self.redis.delete(self.a + ':lease')
        second = self.second.quote_leases([self.a])
        second.__enter__()
        successor = self.redis.get(self.a + ':lease')
        first.__exit__(None, None, None)
        self.assertEqual(self.redis.get(self.a + ':lease'), successor)
        self.assertEqual(self.redis.get(self.a + ':lease:attempts'), '2')
        second.__exit__(None, None, None)

    def test_cache_batch_is_one_pipeline_hits_keep_original_ttl_and_timestamp(self):
        key = build_realtime_key('quote', 'A', 'policy', 'bucket')
        old = {'current': 10, 'updatedAt': '2026-11-13 11:30:00'}
        with patch.object(self.redis, 'pipeline', wraps=self.redis.pipeline) as pipe:
            self.first.put_many({key: old})
            self.assertEqual(pipe.call_count, 1)
        self.redis.pexpire(key, 1500)
        self.second.put_many({key: {'current': 20}})
        self.assertEqual(self.second.get_many([key]), {key: old})
        self.assertLessEqual(self.redis.pttl(key), 1500)
        self.redis.delete(key)  # equivalent to expiry, not stale fallback
        self.assertEqual(self.first.get_many([key]), {})
        self.first.put_many({key: {'current': 20}})
        self.assertEqual(self.second.get_many([key])[key]['current'], 20)

    def test_cache_or_lease_error_does_not_switch_to_process_local_success(self):
        with patch.object(self.redis, 'mget', side_effect=RuntimeError('redis unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'redis unavailable'):
                self.first.get_many([self.a])
        with patch.object(self.redis, 'pipeline', side_effect=RuntimeError('redis unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'redis unavailable'):
                with self.first.quote_leases([self.a]): pass
        self.assertFalse(self.first._memory.leases)
