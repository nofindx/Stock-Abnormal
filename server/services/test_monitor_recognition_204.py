"""Notice recognition proof survives parsing, persistence, merge and detail."""
import os
import tempfile
from datetime import datetime
from unittest import TestCase
from unittest.mock import Mock, patch

from server.services.monitor_service import OfficialMonitorService, SHANGHAI_TZ
from server.resets.decisions import has_auditable_reset_evidence


class MonitorRecognition204Tests(TestCase):
    def test_same_direction_board_counts_keep_actual_window_and_no_price_reason(self):
        for code, count in (("600825", 4), ("301190", 3), ("688585", 3), ("920229", 3)):
            with self.subTest(code=code):
                body = f"连续9个交易日内出现{count}次同向异常波动，属于股票交易严重异常波动。"
                result = OfficialMonitorService._recognize_monitor_text("严重异常波动公告", body, code)
                self.assertEqual(result["monitorType"], "同向严重异动")
                basis = result["recognitionBasis"][0]
                self.assertEqual((basis["ruleKey"], basis["windowDays"], basis["count"]), ("same_direction_10d", 9, count))
                self.assertEqual(basis["evidenceText"], f"连续9个交易日内出现{count}次同向异常波动")
                self.assertNotIn("%", result["ruleText"])
        chinese = OfficialMonitorService._recognize_monitor_text("严重异常波动公告",
            "连续十个交易日内出现四次同向异常波动，属于严重异常波动。", "600825")
        self.assertEqual(chinese["recognitionBasis"][0]["count"], 4)
        self.assertEqual(chinese["recognitionBasis"][0]["windowDays"], 10)
        result = OfficialMonitorService._recognize_monitor_text("异常波动公告", "连续9个交易日内出现4次同向异常波动。", "600825")
        self.assertEqual(result["riskTone"], "", 'count alone without a severe declaration is not a confirmed severe notice')

    def test_long_count_notice_is_not_relabelled_as_a_ten_day_rule(self):
        result = OfficialMonitorService._recognize_monitor_text("严重异常波动公告",
            "连续20个交易日内出现4次同向异常波动，属于严重异常波动。", "600825")
        self.assertEqual(result["monitorType"], "同向严重异动")
        self.assertEqual(result["recognitionBasis"][0]["ruleKey"], "announcement_same_direction")
        self.assertEqual(result["recognitionBasis"][0]["windowDays"], 20)
        self.assertIn("20个交易日", result["ruleText"])
        self.assertNotIn("10日", result["ruleText"])
        evidence = OfficialMonitorService._reset_evidence("严重异常波动公告",
            "连续20个交易日内出现4次同向异常波动，属于严重异常波动。", "600825",
            result["riskTone"], "issuer-disclosure", "https://static.cninfo.com.cn/a.pdf")
        self.assertTrue(evidence, 'confirmed official severe facts must keep reset authority regardless of the calculation window')

    def test_price_and_mixed_recognition_keep_each_basis_and_actual_number(self):
        cases = (("600825", 8, 117.06, "severe_10d"), ("600825", 30, 201, "severe_30d"),
                 ("920229", 10, 150, "severe_10d"), ("920229", 30, -75, "severe_30d"))
        for code, days, value, key in cases:
            with self.subTest(code=code, value=value):
                body = f"连续{days}个交易日内收盘价格{'涨幅' if value > 0 else '跌幅'}偏离值累计达到{abs(value)}%，构成严重异常波动。"
                result = OfficialMonitorService._recognize_monitor_text("严重异常波动公告", body, code)
                basis = result["recognitionBasis"][0]
                self.assertEqual((basis["ruleKey"], basis["windowDays"], basis["deviation"]), (key, days, value))
                self.assertIn(f"{days}个交易日", result["ruleText"])
        body = "连续8个交易日收盘价格涨幅偏离值累计达到117.06%，连续9个交易日内出现4次同向异常波动，属于严重异常波动。"
        result = OfficialMonitorService._recognize_monitor_text("严重异常波动公告", body, "600825")
        self.assertEqual({x["kind"] for x in result["recognitionBasis"]}, {"price_deviation", "same_direction"})
        self.assertIn("117.06%", result["ruleText"])
        self.assertIn("4次同向", result["ruleText"])
        # Do not promote a longer, insufficient price interval to a 10-day fact.
        result = OfficialMonitorService._recognize_monitor_text("严重异常波动公告", "连续20个交易日涨幅偏离值达到100%，属于严重异常波动。", "600825")
        self.assertEqual(result["riskTone"], "")

    def test_collected_count_proof_survives_fact_ledger_primary_merge_and_reset(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "MONITOR_DB_PATH": directory + "/monitor.db", "MONITOR_PDF_DIR": directory + "/pdf"}):
            announcements = Mock()
            announcements.query_broker_risk_alerts.return_value = {"available": True, "items": []}
            announcements.query_zts_risk_alerts.return_value = {"available": True, "items": []}
            announcements.query_market.return_value = {"available": True, "partial": False, "items": [{
                "title": "股票交易严重异常波动公告", "date": "2026-11-19", "stockCode": "600825",
                "stockName": "新华传媒", "url": "https://static.cninfo.com.cn/recognition.pdf", "source": "巨潮资讯"}]}
            announcements.extract_pdf_text.return_value = "截至2026年11月19日收盘，连续9个交易日内出现4次同向异常波动，属于股票交易严重异常波动。"
            service = OfficialMonitorService(client=object(), announcements=announcements)
            self.addCleanup(service.close)
            service._clock = lambda: datetime(2026, 11, 20, 12, tzinfo=SHANGHAI_TZ)
            service._announcement_window_start = lambda *_: "2026-11-01"
            service._cache_active_pdfs = Mock()
            self.assertTrue(service.refresh()["started"])
            fact = service.repository.active_snapshot()["resetFacts"][0]
            self.assertTrue(has_auditable_reset_evidence(fact))
            self.assertEqual(fact["recognitionBasis"][0]["count"], 4)
            self.assertEqual(fact["resetEvidence"]["recognitionBasis"], fact["recognitionBasis"])
            saved = service.repository.rebuild_facts(symbol="600825")[0]
            self.assertEqual(saved["recognitionBasis"], fact["recognitionBasis"])
            broker = dict(fact, sourceId="broker", sourceType="broker-risk-alert", sourcePriority=1,
                          sourceUrl="https://example.test/summary", ruleResetConfirmed=False, recognitionBasis=[], ruleText="")
            official = dict(fact, sourcePriority=2)
            merged = service._collapse_by_stock([broker, official], "2026-11-20")
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["recognitionBasis"], fact["recognitionBasis"])
            self.assertEqual(merged[0]["monitorType"], "同向严重异动")
            self.assertNotIn("100%", merged[0]["ruleText"])
