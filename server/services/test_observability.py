import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from server.services.market_service import MarketService, PREDICTION_RULE_VERSION, SHANGHAI_TZ
from server.services.realtime_quote import RealtimeQuoteClient
from server.services.tushare_client import TushareClient, TushareUnavailable
from server.tools.health_alert_check import collect_alerts


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
    def test_health_ui_is_human_readable_and_escapes_values(self):
        from server.app import _health_ui

        html = _health_ui({"data": {
            "tushareConfigured": True,
            "dataSources": {"tushare": {"totals": {"failures": 0}}, "realtime": {"totals": {"failures": 0}}},
            "prediction": {"today": {"itemCount": 3, "status": "ready", "targetTradeDate": "20261014"}, "next_day": {"itemCount": 2, "status": "ready", "targetTradeDate": "20261015"}},
            "monitor": {"available": True, "itemCount": 0, "snapshotDate": "20261014", "dataQuality": {"activeSource": "18.cn 主源"}},
            "marketCalc": {"available": True, "latestDataDate": "20261014", "dataStage": "formal", "stockBasicCoverage": {"complete": True, "total": 5574}, "predictionMigration": {"compatReadEnabled": False}},
        }})
        self.assertIn("异动查查 · 健康检查", html)
        self.assertIn("当日预测", html)
        self.assertIn("3 条", html)

    def test_health_ui_reports_vnext_and_source_policy_blockers(self):
        from server.app import _health_ui

        html = _health_ui({"data": {
            "tushareConfigured": True,
            "dataSources": {"tushare": {"totals": {"failures": 0}}, "realtime": {"totals": {"failures": 0}}},
            "realtimeCoordination": {"sourcePolicyGate": {"status": "block", "errors": ["source_policy 未配置"]}},
            "vnext": {"contextSigningConfigured": False, "pdfSigningConfigured": False},
            "prediction": {"today": {}, "next_day": {}},
            "monitor": {"available": True, "itemCount": 0},
            "marketCalc": {"available": True, "stockBasicCoverage": {"complete": True}, "predictionMigration": {"compatReadEnabled": False}},
        }})
        self.assertIn("系统异常", html)
        self.assertIn("G26 来源策略门禁阻断", html)
        self.assertIn("vNext context 签名密钥未配置", html)
        self.assertIn("PDF 阅读签名密钥未配置", html)

    def test_prediction_health_reports_age_and_count(self):
        service = MarketService()
        try:
            updated = datetime.now(SHANGHAI_TZ) - timedelta(minutes=5)
            service._calc_repository = SimpleNamespace(
                available=True,
                get_prediction=lambda scope: {
                    "scope": scope, "trade_date": "20260930", "updated_at": updated,
                    "items": [{"ts_code": "000001.SZ"}], "data_stage": "formal",
                    "data_quality": "confirmed", "rule_version": PREDICTION_RULE_VERSION, "last_error": "",
                },
            )
            result = service.prediction_health()
            self.assertTrue(result["today"]["available"])
            self.assertEqual(result["today"]["itemCount"], 1)
            self.assertGreaterEqual(result["today"]["ageSeconds"], 299)
            self.assertLess(result["today"]["ageSeconds"], 360)
        finally:
            service.close()

    def test_prediction_health_uses_intraday_public_dataset(self):
        service = MarketService()
        try:
            updated = datetime.now(SHANGHAI_TZ) - timedelta(hours=48)
            records = {
                ("today", ""): {"scope": "today", "target_trade_date": "20261016", "updated_at": updated, "items": [{"ts_code": "000001.SZ"}], "rule_version": PREDICTION_RULE_VERSION},
                ("next_day", "20261019"): {"scope": "next_day", "base_trade_date": "20261016", "target_trade_date": "20261019", "updated_at": updated, "items": [{"ts_code": "000002.SZ"}], "status": "ready", "dataset_quote_complete": True, "rule_version": PREDICTION_RULE_VERSION},
            }
            service._calc_repository = SimpleNamespace(
                available=True,
                get_prediction=lambda scope, target_trade_date=None: records.get((scope, target_trade_date or "")) or records.get((scope, "")),
                prediction_job_status=lambda _scope: {"status": "ready"},
            )
            service._prediction_state = lambda: {
                "phase": "intraday",
                "todaySourceScope": "next_day",
                "targetTradeDate": "20261019",
                "nextTradeDate": "20261020",
                "previousTradeDate": "20261016",
            }
            result = service.prediction_health(today_is_trade_day=True)
            self.assertEqual(result["today"]["effectiveScope"], "next_day")
            self.assertEqual(result["today"]["targetTradeDate"], "20261019")
            self.assertEqual(result["today"]["itemCount"], 1)
            self.assertFalse(result["today"]["stale"])
            self.assertFalse(result["next_day"]["phaseAvailable"])
            self.assertEqual(result["next_day"]["itemCount"], 0)
        finally:
            service.close()

    def test_health_alerts_cover_failed_dataset_without_redis_requirement(self):
        alerts = collect_alerts({
            "marketCalc": {"lastErrorActive": False},
            "prediction": {"today": {"status": "failed", "lastError": "任务失败", "available": True, "datasetQuoteComplete": True}, "next_day": {}},
            "intradayState": {},
        })
        keys = {item["key"] for item in alerts}
        self.assertEqual(keys, {"prediction_today", "prediction_today_rule_version", "prediction_today_last_error"})

    def test_health_alerts_detect_old_rule_dataset_and_persisted_write_error(self):
        alerts = collect_alerts({
            "marketCalc": {},
            "prediction": {
                "today": {
                    "available": True,
                    "status": "ready",
                    "ruleVersion": "old-rule-v61",
                    "lastError": "Data too long for column 'rule_version'",
                    "datasetQuoteComplete": True,
                },
                "next_day": {
                    "available": True,
                    "status": "ready",
                    "ruleVersion": PREDICTION_RULE_VERSION,
                    "lastError": "",
                    "datasetQuoteComplete": True,
                },
            },
        })
        self.assertEqual(
            {item["key"] for item in alerts},
            {"prediction_today_rule_version", "prediction_today_last_error"},
        )

    def test_health_alerts_report_incomplete_stock_fields_and_legacy_rows(self):
        alerts = collect_alerts({
            "marketCalc": {
                "stockBasicCoverage": {"complete": False},
                "predictionMigration": {"compatReadEnabled": True, "predictionCacheRows": 2},
            },
            "prediction": {"today": {}, "next_day": {}},
            "intradayState": {"backend": "redis", "shared": True},
        })
        self.assertEqual({item["key"] for item in alerts}, {"stock_basic_coverage", "prediction_cache_migration"})

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
                "index_code": "399107.SZ", "latest_close": 100.0,
                "as_of_trade_date": "20260830",
                "index_return_vector": vector,
            },
        )
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: quotes)
        service._future_trade_dates = lambda _latest, _count: []
        return service

    def test_mysql_base_and_both_realtime_quotes_are_reported_as_realtime(self):
        service = self._service({
            "000001.SZ": {"current": 11.0, "pctChg": 10.0, "updatedAt": "2026-09-30T10:00:00"},
            "399107.SZ": {"current": 101.0, "pctChg": 1.0, "updatedAt": "2026-09-30T10:00:00"},
        })
        try:
            result = service._detail_from_repository(
                service._stock_rows()[0],
                {"latest_close": 10.0, "as_of_trade_date": "20260830", "data_stage": "formal", "stock_return_vector": [
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
                {"latest_close": 10.0, "as_of_trade_date": "20260830", "data_stage": "formal", "stock_return_vector": [
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
