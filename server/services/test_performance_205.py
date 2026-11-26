"""Concurrency/performance contracts without production sources or databases."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import TestCase
from unittest.mock import Mock, patch

from server.services.tushare_client import TushareClient
from server.services.read_performance import ReadPerformance


class SourceCacheConcurrencyTests(TestCase):
    def test_cached_calendar_and_different_key_are_not_blocked_by_network_loader(self):
        client = TushareClient(token="isolated-test")
        client._cached("calendar", 600, lambda: ["cached"])
        entered, release = Event(), Event()
        def slow():
            entered.set()
            if not release.wait(3):
                raise AssertionError("test loader never released")
            return ["slow"]
        with ThreadPoolExecutor(max_workers=3) as pool:
            owner = pool.submit(client._cached, "background", 600, slow)
            try:
                self.assertTrue(entered.wait(1))
                hit = pool.submit(client._cached, "calendar", 600, lambda: self.fail("unexpected miss"))
                other = pool.submit(client._cached, "other", 600, lambda: ["independent"])
                self.assertEqual(hit.result(timeout=1), ["cached"])
                self.assertEqual(other.result(timeout=1), ["independent"])
            finally:
                release.set()
            self.assertEqual(owner.result(), ["slow"])

    def test_same_key_misses_merge_and_return_independent_copies(self):
        client = TushareClient(token="isolated-test")
        entered, release = Event(), Event()
        def slow():
            entered.set()
            release.wait(3)
            return ["original"]
        loader = Mock(side_effect=slow)
        with ThreadPoolExecutor(max_workers=5) as pool:
            pending = [pool.submit(client._cached, "same", 60, loader) for _ in range(5)]
            self.assertTrue(entered.wait(1))
            release.set()
            results = [future.result() for future in pending]
        self.assertEqual(loader.call_count, 1)
        results[0].append("client mutation")
        self.assertEqual(results[1], ["original"])
        self.assertEqual(client._cached("same", 60, loader), ["original"])

    def test_failure_is_not_cached_and_success_ttl_starts_after_load(self):
        client = TushareClient(token="isolated-test")
        with self.assertRaisesRegex(RuntimeError, "upstream"):
            client._cached("failed", 60, lambda: (_ for _ in ()).throw(RuntimeError("upstream")))
        self.assertFalse(client._cache_inflight)
        self.assertEqual(client._cached("failed", 60, lambda: "retried"), "retried")
        clock = [100]
        def load():
            clock[0] = 400
            return ["loaded"]
        with patch("server.services.tushare_client.time.monotonic", side_effect=lambda: clock[0]):
            client._cached("slow", 60, load)
            clock[0] = 450
            self.assertEqual(client._cached("slow", 60, lambda: self.fail("premature expiry")), ["loaded"])


class ReadTimingTests(TestCase):
    def test_timing_is_bounded_to_known_stages_and_counts_failed_work(self):
        stats = ReadPerformance()
        with stats.measure("context.capture"):
            pass
        with self.assertRaises(RuntimeError):
            with stats.measure("context.capture"):
                raise RuntimeError("not serialized")
        self.assertEqual(stats.snapshot()["context.capture"]["count"], 2)
        with self.assertRaises(ValueError):
            with stats.measure("token-or-stock-must-not-be-a-label"):
                pass
        self.assertEqual(set(stats.snapshot()), {"context.capture"})
