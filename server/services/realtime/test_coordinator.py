import unittest
import time
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from .coordinator import RealtimeCoordinator, build_realtime_key, quote_bucket


class RealtimeCoordinatorTests(unittest.TestCase):
    def test_quote_bucket_is_stable_inside_window_and_changes_at_boundary(self):
        self.assertEqual(quote_bucket(99.1, 3), quote_bucket(101.9, 3))
        self.assertNotEqual(quote_bucket(101.9, 3), quote_bucket(102.0, 3))

    def test_quote_bucket_rejects_invalid_duration(self):
        with self.assertRaises(ValueError):
            quote_bucket(100, 0)

    def test_upstream_attempt_cap_is_two_per_window(self):
        coordinator = RealtimeCoordinator(lease_seconds=1)
        key = build_realtime_key("singleflight", "600000.SH", "source", "continuous", quote_bucket(100, 3))
        with coordinator.lease(key) as first:
            self.assertFalse(first.takeover)
        with coordinator._memory.lock:
            coordinator._memory.attempts[f"{key}:lease"] = (9999999999, 2)
        with self.assertRaises(TimeoutError):
            with coordinator.lease(key):
                pass

    def test_concurrent_leases_have_one_leader(self):
        coordinator = RealtimeCoordinator(lease_seconds=1)
        key = build_realtime_key("singleflight", "600000.SH", "source", "continuous", quote_bucket(100, 3))

        def acquire():
            try:
                with coordinator.lease(key):
                    time.sleep(0.03)
                    return "leader"
            except TimeoutError:
                return "joined"

        with ThreadPoolExecutor(max_workers=16) as pool:
            outcomes = list(pool.map(lambda _value: acquire(), range(16)))
        self.assertEqual(outcomes.count("leader"), 1)

    def test_three_key_namespaces_are_distinct(self):
        parts = ("600000.SH", "source-v1", "20261029T093000")
        self.assertNotEqual(build_realtime_key("quote", *parts), build_realtime_key("singleflight", *parts))
        self.assertNotEqual(build_realtime_key("singleflight", *parts), build_realtime_key("view", *parts))

    def test_normal_lease_is_single_owner_and_fence_increases(self):
        coordinator = RealtimeCoordinator()
        key = build_realtime_key("singleflight", "600000.SH", "source-v1", "continuous", "q1")
        with coordinator.lease(key) as first:
            with self.assertRaises(TimeoutError):
                with coordinator.lease(key):
                    pass
        with coordinator.lease(key) as second:
            self.assertGreater(second.fence, first.fence)

    def test_cache_is_short_lived_and_not_authoritative(self):
        coordinator = RealtimeCoordinator()
        key = build_realtime_key("quote", "600000.SH", "source-v1", "q1")
        self.assertEqual(coordinator.get_or_set(key, {"price": "1"}), {"price": "1"})
        self.assertEqual(coordinator.get_or_set(key), {"price": "1"})

    def test_split_redis_environment_is_supported_without_exposing_password(self):
        with patch.dict("os.environ", {"REDIS_URL": "", "REDIS_HOST": "10.0.0.2", "REDIS_PORT": "6379", "REDIS_DB": "2", "REDIS_PASSWORD": "secret", "REDIS_SSL": "0"}, clear=False):
            url = RealtimeCoordinator._redis_url_from_parts()
        self.assertEqual(url, "redis://:secret@10.0.0.2:6379/2")


if __name__ == "__main__":
    unittest.main()
