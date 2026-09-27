import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import Mock

from server.services.monitor_service import OfficialMonitorService


class MonitorWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = os.environ.get("MONITOR_DB_PATH")
        os.environ["MONITOR_DB_PATH"] = os.path.join(self.temp_dir.name, "monitor.db")
        self.service = OfficialMonitorService(client=object(), announcements=object())

    def tearDown(self):
        self.service.close()
        if self.previous_db_path is None:
            os.environ.pop("MONITOR_DB_PATH", None)
        else:
            os.environ["MONITOR_DB_PATH"] = self.previous_db_path
        self.temp_dir.cleanup()

    @staticmethod
    def event(start, end, risk="ordinary"):
        return {
            "sourceId": f"{start}-{risk}", "monitorKey": f"{start}-{risk}",
            "symbol": "000993", "name": "闽东电力", "title": "来源记录",
            "announcementDate": start, "monitorStart": start[5:], "monitorStartDate": start,
            "monitorEnd": end[5:], "monitorEndDate": end, "daysTotal": 22,
            "monitorType": "风险提示" if risk == "ordinary" else "10日严重异动",
            "riskTone": risk, "sourceType": "broker-risk-alert" if risk == "ordinary" else "issuer-disclosure",
            "source": "测试来源", "sourceUrl": f"https://example.test/{start}", "contentHash": start,
        }

    def test_reference_window_uses_next_day_for_broker_alert_and_announcement_day_for_severe_notice(self):
        dates = [
            (date(2026, 9, 1) + timedelta(days=index)).strftime("%Y-%m-%d")
            for index in range(60)
            if (date(2026, 9, 1) + timedelta(days=index)).weekday() < 5
            and not date(2026, 9, 1) + timedelta(days=index) in {
                date(2026, 9, 25),
                *{date(2026, 10, day) for day in range(1, 8)},
            }
        ]
        self.service._trade_dates = lambda source_date, days: dates
        self.assertEqual(self.service._monitor_period("2026-09-22", "broker-risk-alert"), ("2026-09-23", "2026-10-14"))
        self.assertEqual(self.service._monitor_period("2026-09-23", "issuer-disclosure", "severe-10d"), ("2026-09-23", "2026-10-14"))
        self.assertEqual(self.service._monitor_period("2026-09-23", "broker-risk-alert"), ("2026-09-24", "2026-10-15"))

    def test_end_date_is_current_zero_days_and_next_day_is_removed(self):
        self.assertFalse(self.service._is_expired("2026-09-28", "2026-09-28"))
        self.assertEqual(self.service._remaining_natural_days("2026-10-14", "2026-09-27"), 17)
        self.assertTrue(self.service._is_expired("2026-09-28", "2026-09-29"))

    def test_same_type_overlapping_period_uses_latest_record_but_different_types_remain_separate(self):
        risk_first = self.event("2026-09-23", "2026-10-14", "ordinary")
        risk_renewal = self.event("2026-10-10", "2026-10-31", "ordinary")
        severe = self.event("2026-09-23", "2026-10-14", "severe-10d")

        result = self.service._collapse_by_stock([risk_first, risk_renewal, severe], "2026-10-10")

        self.assertEqual(len(result), 2)
        risk = next(item for item in result if item["riskTone"] == "ordinary")
        severe_result = next(item for item in result if item["riskTone"] == "severe-10d")
        self.assertEqual(risk["monitorStartDate"], "2026-10-10")
        self.assertEqual(risk["monitorEndDate"], "2026-10-31")
        self.assertEqual(severe_result["monitorEndDate"], "2026-10-14")

    def test_reference_stocks_have_the_attachment_periods(self):
        dates = [
            (date(2026, 9, 1) + timedelta(days=index)).strftime("%Y-%m-%d")
            for index in range(60)
            if (date(2026, 9, 1) + timedelta(days=index)).weekday() < 5
            and not date(2026, 9, 1) + timedelta(days=index) in {
                date(2026, 9, 25),
                *{date(2026, 10, day) for day in range(1, 8)},
            }
        ]
        self.service._trade_dates = lambda source_date, days: dates
        periods = {
            "五洲医疗": self.service._monitor_period("2026-09-22", "broker-risk-alert"),
            "闽东电力": self.service._monitor_period("2026-09-22", "broker-risk-alert"),
            "天普股份": self.service._monitor_period("2026-09-23", "broker-risk-alert"),
        }
        self.assertEqual(periods["五洲医疗"], ("2026-09-23", "2026-10-14"))
        self.assertEqual(periods["闽东电力"], ("2026-09-23", "2026-10-14"))
        self.assertEqual(periods["天普股份"], ("2026-09-24", "2026-10-15"))

    def test_broker_page_noise_does_not_upgrade_risk_prompt(self):
        noisy_page = "交易所将对以上证券的异常交易行为进行从严认定；页面脚本中的100%不是异动规则。"
        self.assertEqual(OfficialMonitorService._broker_monitor_type("关于股票交易风险提示的公告", noisy_page), ("风险提示", "ordinary"))
        self.assertEqual(OfficialMonitorService._broker_monitor_type("关于股票交易严重异常波动的风险提示", "严重异常波动，但未披露10日或30日阈值"), ("", ""))

    def test_issuer_ordinary_risk_prompt_is_not_a_monitor_record(self):
        self.assertEqual(OfficialMonitorService._monitor_type("股票交易异常波动风险提示公告", "公司基本面未发生重大变化"), ("", ""))
        self.assertEqual(OfficialMonitorService._monitor_type("股票交易严重异常波动公告", "严重异常波动，但未披露10日或30日阈值"), ("", ""))
        self.assertEqual(OfficialMonitorService._monitor_type("股票交易严重异常波动公告", "连续10个交易日涨幅偏离值累计达到100%"), ("10日严重异动", "severe-10d"))
        self.assertEqual(OfficialMonitorService._monitor_type("股票交易异常波动公告", "连续30个交易日涨幅偏离值累计达到200%"), ("30日严重异动", "severe-30d"))

    def test_broker_alert_requires_security_monitoring_wording(self):
        self.assertFalse(OfficialMonitorService._is_accepted_broker_alert("关于股票交易风险提示的公告", "将视情况从重采取被列为重点监控账户措施"))
        self.assertFalse(OfficialMonitorService._is_accepted_broker_alert("关于股票交易风险提示的公告", "交易所已将证券列为重点监控证券"))
        self.assertTrue(OfficialMonitorService._is_accepted_broker_alert("关于股票交易风险提示的公告", "交易所将对以上证券的异常交易行为进行从严认定"))

    def test_semantic_threshold_variants_are_supported(self):
        self.assertEqual(OfficialMonitorService._monitor_type("风险提示", "10 个交易日内涨幅偏离值累计超过100％以上"), ("10日严重异动", "severe-10d"))
        self.assertEqual(OfficialMonitorService._monitor_type("风险提示", "30个交易日内日收盘价格涨幅偏离值累计达到200.15%"), ("30日严重异动", "severe-30d"))

    def test_history_endpoint_is_removed(self):
        with self.assertRaises(ValueError):
            self.service.read("history", "all")

    def test_backup_becomes_primary_when_primary_is_unavailable(self):
        announcements = Mock()
        announcements.query_broker_risk_alerts.return_value = {
            "available": False, "items": [], "error": "primary unavailable"
        }
        announcements.query_market.return_value = {
            "available": True, "partial": False, "items": [{
                "title": "股票交易严重异常波动公告", "date": "2026-09-27", "stockCode": "000993",
                "stockName": "闽东电力", "url": "https://static.cninfo.com.cn/a.PDF",
                "source": "巨潮资讯",
            }]
        }
        announcements.extract_pdf_text.return_value = "连续10个交易日涨幅偏离值累计达到100%"
        service = OfficialMonitorService(client=object(), announcements=announcements)
        try:
            result = service.refresh()
            self.assertTrue(result["started"])
            announcements.query_market.assert_called_once()
            response = service.read("current", "all")
            self.assertFalse(response["refreshing"])
            self.assertEqual(len(response["items"]), 1)
            self.assertEqual(response["dataQuality"]["activeSource"], "issuer_disclosure_backup")
        finally:
            service.close()

    def test_both_sources_unavailable_keep_old_snapshot_or_return_empty_error(self):
        announcements = Mock()
        announcements.query_broker_risk_alerts.return_value = {"available": False, "items": [], "error": "primary down"}
        announcements.query_market.return_value = {"available": False, "partial": True, "items": [], "error": "backup down"}
        service = OfficialMonitorService(client=object(), announcements=announcements)
        try:
            result = service.refresh()
            self.assertIn("均不可用", result["error"])
            response = service.read("current", "all")
            self.assertFalse(response["refreshing"])
            self.assertEqual(response["items"], [])
            self.assertIn("均不可用", response["error"])
        finally:
            service.close()

    def test_backup_is_called_only_after_primary_succeeds(self):
        announcements = Mock()
        announcements.query_broker_risk_alerts.return_value = {
            "available": True,
            "items": [{
                "title": "风险提示", "date": "2026-09-27", "stockCode": "000993",
                "stockName": "闽东电力", "url": "https://18.cn/a/1",
                "source": "东方财富证券 18.cn", "sourceType": "broker-risk-alert",
                "sourceRole": "broker_primary", "sourcePriority": 1,
                "sourceLabel": "18.cn 主源",
                "body": "交易所将对以上证券的异常交易行为进行从严认定",
            }],
        }
        announcements.query_market.return_value = {"available": False, "partial": True, "items": []}
        service = OfficialMonitorService(client=object(), announcements=announcements)
        try:
            result = service.refresh()
            self.assertTrue(result["started"])
            announcements.query_market.assert_called_once()
            snapshot = service.repository.active_snapshot()
            self.assertIsNotNone(snapshot)
            self.assertEqual(len(snapshot["items"]), 1)
            self.assertEqual(snapshot["dataQuality"]["sourceHealth"]["issuerBackup"], "degraded")
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
