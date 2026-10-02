import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from server.services.market_service import MarketService, SHANGHAI_TZ
from server.services.realtime_quote import RealtimeQuoteClient
from server.services.tushare_client import TushareClient, TushareUnavailable


class TushareHealthTests(unittest.TestCase):
    def test_call_records_success_and_failure_without_token(self):
        client = TushareClient(token="secret-token")
        api = SimpleNamespace(ok=lambda **_kwargs: {"ok": True})
        client._api = lambda: api
        self.assertEqual(client.call("ok", ts_code="000001.SZ"), {"ok": True})

        def fail(**_kwargs):
            raise RuntimeError("secret-token upstream failure")

        api.fail = fail
        with self.assertRaises(TushareUnavailable):
            client.call("fail")
        stats = client.health_stats()
        self.assertEqual(stats["totals"]["requests"], 2)
        self.assertEqual(stats["totals"]["successes"], 1)
        self.assertEqual(stats["totals"]["failures"], 1)
        self.assertNotIn("secret-token", stats["methods"]["fail"]["lastError"])


class RealtimeHealthTests(unittest.TestCase):
    def test_source_stats_record_fallback_attempts(self):
        client = RealtimeQuoteClient()
        client._fetch_tencent = Mock(side_effect=RuntimeError("timeout"))
        client._fetch_sina = Mock(return_value={"000001.SZ": {
            "current": 10.0, "preClose": 9.0, "pctChg": 11.11,
        }})
        client._fetch_eastmoney = Mock(return_value={})
        result = client.fetch_many(["000001.SZ"])
        self.assertIn("000001.SZ", result)
        stats = client.health_stats()
        self.assertEqual(stats["sources"]["tencent"]["failures"], 1)
        self.assertEqual(stats["sources"]["sina"]["successes"], 1)
        self.assertNotIn("000001.SZ", str(stats))


class PredictionHealthTests(unittest.TestCase):
    def test_prediction_health_reports_age_and_count(self):
        service = MarketService()
        try:
            updated = datetime.now(SHANGHAI_TZ) - timedelta(minutes=5)
            service._calc_repository = SimpleNamespace(
                available=True,
                get_prediction=lambda scope: {
                    "scope": scope, "trade_date": "20260930", "updated_at": updated,
                    "items": [{"ts_code": "000001.SZ"}], "data_stage": "formal",
                    "data_quality": "confirmed", "rule_version": "test", "last_error": "",
                },
            )
            result = service.prediction_health()
            self.assertTrue(result["today"]["available"])
            self.assertEqual(result["today"]["itemCount"], 1)
            self.assertGreaterEqual(result["today"]["ageSeconds"], 299)
            self.assertLess(result["today"]["ageSeconds"], 360)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
