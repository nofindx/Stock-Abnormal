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


class DetailDataQualityTests(unittest.TestCase):
    def _service(self, quotes):
        service = MarketService()
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        vector = [{"date": value, "return": 0.0} for value in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            get_index=lambda _code: {
                "index_code": "399001.SZ", "latest_close": 100.0,
                "index_return_vector": vector,
            },
        )
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: quotes)
        service._future_trade_dates = lambda _latest, _count: []
        return service

    def test_mysql_base_and_both_realtime_quotes_are_reported_as_realtime(self):
        service = self._service({
            "000001.SZ": {"current": 11.0, "pctChg": 10.0, "updatedAt": "2026-09-30T10:00:00"},
            "399001.SZ": {"current": 101.0, "pctChg": 1.0, "updatedAt": "2026-09-30T10:00:00"},
        })
        try:
            result = service._detail_from_repository(
                service._stock_rows()[0],
                {"latest_close": 10.0, "as_of_trade_date": "20260929", "data_stage": "formal", "stock_return_vector": [
                    {"date": f"2026-08-{index:02d}", "return": 0.0} for index in range(1, 31)
                ]},
            )
            self.assertTrue(result["dataQuality"]["realtime"])
            self.assertFalse(result["dataQuality"]["fallback"])
        finally:
            service.close()

    def test_realtime_failure_keeps_mysql_result_and_marks_unavailable(self):
        service = self._service({})
        try:
            result = service._detail_from_repository(
                service._stock_rows()[0],
                {"latest_close": 10.0, "as_of_trade_date": "20260929", "data_stage": "formal", "stock_return_vector": [
                    {"date": f"2026-08-{index:02d}", "return": 0.0} for index in range(1, 31)
                ]},
            )
            self.assertEqual(result["currentPrice"], "10.00")
            self.assertFalse(result["dataQuality"]["realtime"])
            self.assertTrue(result["dataQuality"]["fallback"])
            self.assertEqual(result["dataQuality"]["message"], "实时行情暂不可用")
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
