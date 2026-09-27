import os
import tempfile
import unittest

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
        self.assertEqual(self.service._monitor_period("2026-09-22", "broker-risk-alert"), ("2026-09-23", "2026-10-14"))
        self.assertEqual(self.service._monitor_period("2026-09-23", "issuer-disclosure"), ("2026-09-23", "2026-10-14"))
        self.assertEqual(self.service._monitor_period("2026-09-23", "broker-risk-alert"), ("2026-09-24", "2026-10-15"))

    def test_end_date_is_current_zero_days_and_next_day_is_history(self):
        self.assertFalse(self.service._is_history("2026-09-28", "2026-09-28"))
        self.assertEqual(self.service._remaining_natural_days("2026-10-14", "2026-09-27"), 17)
        self.assertTrue(self.service._is_history("2026-09-28", "2026-09-29"))

    def test_same_type_overlapping_period_extends_but_different_types_remain_separate(self):
        risk_first = self.event("2026-09-23", "2026-10-14", "ordinary")
        risk_renewal = self.event("2026-10-10", "2026-10-31", "ordinary")
        severe = self.event("2026-09-23", "2026-10-14", "severe-10d")

        result = self.service._collapse_by_stock([risk_first, risk_renewal, severe], "2026-10-10")

        self.assertEqual(len(result), 2)
        risk = next(item for item in result if item["riskTone"] == "ordinary")
        severe_result = next(item for item in result if item["riskTone"] == "severe-10d")
        self.assertEqual(risk["monitorStartDate"], "2026-09-23")
        self.assertEqual(risk["monitorEndDate"], "2026-10-31")
        self.assertEqual(severe_result["monitorEndDate"], "2026-10-14")

    def test_reference_stocks_have_the_attachment_periods(self):
        periods = {
            "五洲医疗": self.service._monitor_period("2026-09-22", "broker-risk-alert"),
            "闽东电力": self.service._monitor_period("2026-09-22", "broker-risk-alert"),
            "天普股份": self.service._monitor_period("2026-09-23", "broker-risk-alert"),
        }
        self.assertEqual(periods["五洲医疗"], ("2026-09-23", "2026-10-14"))
        self.assertEqual(periods["闽东电力"], ("2026-09-23", "2026-10-14"))
        self.assertEqual(periods["天普股份"], ("2026-09-24", "2026-10-15"))


if __name__ == "__main__":
    unittest.main()
