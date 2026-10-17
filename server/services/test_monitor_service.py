import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock

from server.services.monitor_service import MONITOR_RULE_VERSION, OfficialMonitorService, SHANGHAI_TZ
from server.services.announcement_service import AnnouncementService


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

    def test_cninfo_query_market_includes_bse_column(self):
        service = AnnouncementService()
        columns = []

        def request(payload):
            columns.append(payload["column"])
            return {"announcements": [], "hasMore": False}

        service._request = request
        result = service.query_market("2026-09-30", "2026-10-07")
        self.assertTrue(result["available"])
        self.assertIn("bse", columns)

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

    def test_bse_severe_notice_starts_on_next_trade_day(self):
        dates = [
            "2026-09-28", "2026-09-29", "2026-09-30",
            "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13",
            "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20",
        ]
        self.service._trade_dates = lambda source_date, days: dates
        self.assertEqual(
            self.service._monitor_period("2026-09-29", "issuer-disclosure", "severe-10d", "920229"),
            ("2026-09-30", "2026-10-20"),
        )

    def test_severe_period_uses_last_event_trade_date_from_announcement_body(self):
        dates = [
            "2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13",
            "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20",
            "2026-10-21", "2026-10-22",
        ]
        self.service._trade_dates = lambda source_date, days: dates
        body = "公司股票于2026年9月16日至2026年9月30日连续10个交易日内收盘价累计涨幅偏离值达到100%"
        event_end = self.service._event_end_date("2026-10-08", body)
        self.assertEqual(event_end, "2026-09-30")
        self.assertEqual(
            self.service._monitor_period_dates(event_end, "issuer-disclosure", "severe-10d", "688137", event_end_date=event_end)[-1],
            "2026-10-21",
        )

    def test_monitor_noise_excludes_st_delisting_but_keeps_normal_bse_nine(self):
        self.assertFalse(self.service._is_noise_stock("920229", "世纪数码"))
        self.assertTrue(self.service._is_noise_stock("510300", "沪深300ETF"))
        self.assertTrue(self.service._is_noise_stock("123456", "测试基金"))
        self.assertTrue(self.service._is_noise_stock("920575", "*ST康乐"))
        self.assertTrue(self.service._is_noise_stock("301139", "元道退"))

    def test_thirty_day_detection_window_does_not_create_thirty_day_monitor_period(self):
        dates = [
            (date(2026, 8, 21) + timedelta(days=index)).strftime("%Y-%m-%d")
            for index in range(45)
            if (date(2026, 8, 21) + timedelta(days=index)).weekday() < 5
        ]
        self.service._trade_dates = lambda source_date, days: dates
        # 哈药股份类 30 日偏离触发公告，监管期仍是触发日起 10 个交易日。
        start, end = self.service._monitor_period("2026-08-21", "issuer-disclosure", "severe-30d")
        self.assertEqual(start, "2026-08-21")
        self.assertEqual(end, "2026-09-03")
        self.assertEqual(len(self.service._monitor_period_dates("2026-08-21", "issuer-disclosure", "severe-30d")), 10)

    def test_end_date_is_current_zero_days_and_next_day_is_removed(self):
        self.assertFalse(self.service._is_expired("2026-09-28", "2026-09-28"))
        self.assertEqual(self.service._remaining_natural_days("2026-10-14", "2026-09-27"), 17)
        self.assertTrue(self.service._is_expired("2026-09-28", "2026-09-29"))

    def test_health_count_matches_current_active_items(self):
        today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
        yesterday = (datetime.now(SHANGHAI_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
        self.service.repository.active_snapshot = lambda: {
            "snapshotDate": today,
            "updatedAt": datetime.now(SHANGHAI_TZ).isoformat(timespec="minutes"),
            "items": [{"monitorEndDate": yesterday}, {"monitorEndDate": today}],
        }
        self.service.repository.states = lambda: {}
        health = self.service.health_stats(today_is_trade_day=True)
        self.assertEqual(health["itemCount"], 1)

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

    def test_same_day_broker_primary_keeps_official_body_reset_confirmation(self):
        broker = self.event("2026-10-08", "2026-10-22", "severe-10d")
        broker.update({"sourceType": "broker-risk-alert", "sourcePriority": 1})
        issuer = self.event("2026-10-08", "2026-10-22", "severe-10d")
        issuer.update({
            "sourceType": "issuer-disclosure", "sourcePriority": 2,
            "ruleResetConfirmed": True, "ruleResetDate": "20261009",
            "monitorDates": ["2026-10-09", "2026-10-12"],
        })

        result = self.service._collapse_by_stock([broker, issuer], "2026-10-17")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sourceType"], "broker-risk-alert")
        self.assertTrue(result[0]["ruleResetConfirmed"])
        self.assertEqual(result[0]["ruleResetDate"], "20261009")

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

    def test_remaining_days_use_frozen_trade_dates(self):
        dates = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08"]
        self.assertEqual(self.service._remaining_trading_days(dates, "2026-09-28"), 4)
        self.assertEqual(self.service._remaining_trading_days(dates, "2026-09-29"), 3)
        self.assertEqual(self.service._remaining_trading_days(dates, "2026-10-01"), 1)

    def test_broker_page_noise_does_not_upgrade_risk_prompt(self):
        noisy_page = "交易所将对以上证券的异常交易行为进行从严认定；页面脚本中的100%不是异动规则。"
        self.assertEqual(OfficialMonitorService._broker_monitor_type("关于股票交易风险提示的公告", noisy_page), ("风险提示", "ordinary"))
        self.assertEqual(OfficialMonitorService._broker_monitor_type("关于股票交易严重异常波动的风险提示", "严重异常波动，但未披露10日或30日阈值"), ("", ""))

    def test_issuer_ordinary_risk_prompt_is_not_a_monitor_record(self):
        self.assertEqual(OfficialMonitorService._monitor_type("股票交易异常波动风险提示公告", "公司基本面未发生重大变化"), ("", ""))
        self.assertEqual(OfficialMonitorService._monitor_type("股票交易严重异常波动公告", "严重异常波动，但未披露10日或30日阈值"), ("", ""))
        self.assertEqual(OfficialMonitorService._monitor_type("股票交易严重异常波动公告", "连续10个交易日涨幅偏离值累计达到100%"), ("10日严重异动", "severe-10d"))
        self.assertEqual(OfficialMonitorService._monitor_type("股票交易异常波动公告", "连续30个交易日涨幅偏离值累计达到200%"), ("30日严重异动", "severe-30d"))

    def test_issuer_repurchase_risk_prompt_without_exchange_wording_is_ignored(self):
        title = "关于股票交易停牌核查结果、股票交易风险提示暨复牌的公告"
        body = "公司股票将于2026年10月16日开市起复牌，敬请投资者关注交易风险。"
        self.assertEqual(OfficialMonitorService._issuer_monitor_type(title, body), ("", ""))

    def test_issuer_risk_prompt_requires_exchange_strict_wording(self):
        self.assertEqual(
            OfficialMonitorService._issuer_monitor_type(
                "关于股票交易风险提示的公告",
                "交易所将对以上证券的异常交易行为进行进一步从严认定。",
            ),
            ("风险提示", "ordinary"),
        )
        self.assertEqual(
            OfficialMonitorService._zts_monitor_type(
                "关于新华传媒（600825）的交易风险提示",
                "上交所将对上述证券上的异常交易行为进行进一步从严认定。",
                "ordinary",
            ),
            ("风险提示", "ordinary"),
        )
        self.assertEqual(
            OfficialMonitorService._zts_monitor_type("关于天普股份（605255）的交易风险提示", "请注意二级市场交易风险。", "ordinary"),
            ("", ""),
        )

    def test_zts_risk_labels_are_separate(self):
        self.assertEqual(
            OfficialMonitorService._zts_monitor_type(
                "关于某股票(600000)交易风险提示",
                "交易所将对以上证券的异常交易行为进行进一步从严认定。",
            ),
            ("风险提示", "ordinary"),
        )
        self.assertEqual(
            OfficialMonitorService._zts_monitor_type("关于“某股票”(600000)进入退市整理期的风险警示", "", "delisting-warning"),
            ("退市倒计时", "delisting-warning"),
        )

    def test_delisting_countdown_uses_entry_and_last_trading_dates_from_article(self):
        body = (
            "该股票将自2026年9月30日起进入退市整理期，退市整理期为15个交易日，"
            "预计最后交易日期为2026年10月27日。"
        )
        self.assertEqual(
            OfficialMonitorService._delisting_dates_from_body(body),
            ("2026-09-30", "2026-10-27"),
        )
        calendar = [
            "2026-09-30", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13",
            "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20",
            "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27",
        ]
        self.service._trade_dates = lambda _source_date, _days: calendar
        dates, start, end = self.service._delisting_period_dates(body, "2026-09-23", "301139")
        self.assertEqual((start, end), ("2026-09-30", "2026-10-27"))
        self.assertEqual(len(dates), 15)

    def test_zts_source_fetches_transaction_and_delisting_alerts(self):
        service = AnnouncementService()
        responses = {
            "/article/getArticleList": {
                "rows": [
                    {"id": 1, "title": "关于“新华传媒”（600825）的交易风险提示", "publishTime": "2026-10-16 09:00:00"},
                    {"id": 2, "title": "关于“*ST元道”(301139)进入退市整理期的风险警示", "publishTime": "2026-09-23 08:46:52"},
                ]
            },
            "/article/getArticleDetail": {
                "data": {"id": 1, "title": "关于“新华传媒”（600825）的交易风险提示", "publishTime": "2026-10-16 09:00:00", "content": "交易风险"}
            },
        }
        def request(path, params):
            if params.get("id") == 2:
                return {"data": {"id": 2, "title": "关于“*ST元道”(301139)进入退市整理期的风险警示", "publishTime": "2026-09-23 08:46:52", "content": "自2026年9月30日起进入退市整理期，预计最后交易日期为2026年10月27日。"}}
            return responses[path]
        service._zts_request = request
        result = service.query_zts_risk_alerts("2026-09-01", "2026-10-17")
        self.assertTrue(result["available"])
        self.assertEqual([item["riskToneHint"] for item in result["items"]], ["ordinary", "delisting-warning"])

    def test_monitor_read_adds_standard_ts_code_for_stock_detail_navigation(self):
        snapshot = {"snapshotId": "x", "snapshotDate": "2026-10-17", "updatedAt": "2026-10-17 00:00", "items": [{
            "monitorKey": "x", "symbol": "600825", "name": "新华传媒", "monitorEndDate": "2026-10-29",
            "riskTone": "ordinary", "monitorDates": [], "monitorType": "风险提示",
        }], "dataQuality": {}}
        self.service.repository.active_snapshot = lambda: snapshot
        result = self.service.read("current", "all")
        self.assertEqual(result["items"][0]["ts_code"], "600825.SH")

    def test_delisting_filter_bypasses_all_noise_rules_but_risk_and_severe_do_not(self):
        snapshot = {"snapshotId": "x", "snapshotDate": "2026-10-17", "updatedAt": "2026-10-17 00:00", "dataQuality": {}, "items": [
            {"monitorKey": "risk", "symbol": "002731", "name": "*ST萃华", "monitorEndDate": "2099-01-01", "riskTone": "ordinary", "monitorType": "风险提示"},
            {"monitorKey": "severe", "symbol": "002731", "name": "*ST萃华", "monitorEndDate": "2099-01-01", "riskTone": "severe-10d", "monitorType": "10日严重异动"},
            {"monitorKey": "delisting", "symbol": "002731", "name": "*ST萃华", "monitorEndDate": "2099-01-01", "riskTone": "delisting-warning", "monitorType": "退市整理风险警示"},
        ]}
        self.service.repository.active_snapshot = lambda: snapshot
        all_items = self.service.read("current", "all")["items"]
        delisting_items = self.service.read("current", "delisting")["items"]
        risk_items = self.service.read("current", "risk")["items"]
        severe_items = self.service.read("current", "severe")["items"]
        self.assertEqual([item["monitorKey"] for item in all_items], ["delisting"])
        self.assertEqual([item["monitorKey"] for item in delisting_items], ["delisting"])
        self.assertEqual(risk_items, [])
        self.assertEqual(severe_items, [])

    def test_bse_negative_severe_deviation_is_not_monitor_record(self):
        body = "股票连续4个交易日内日收盘价跌幅偏离值累计达到-61.39%，属于股票交易严重异常波动情形。"
        self.assertEqual(
            OfficialMonitorService._monitor_type("股票交易严重异常波动公告", body),
            ("", ""),
        )
        self.assertEqual(
            OfficialMonitorService._monitor_type(
                "郑州新世纪数码科技股份有限公司股票交易严重异常波动公告",
                "连续4个交易日内日收盘价跌幅偏离值累计达到-61.39%，属于股票交易严重异常波动情形。",
            ),
            ("", ""),
        )

    def test_bse_positive_severe_deviation_with_actual_short_window_is_monitor_record(self):
        body = "股票连续4个交易日内日收盘价涨幅偏离值累计达到105.39%，属于股票交易严重异常波动情形。"
        self.assertEqual(
            OfficialMonitorService._monitor_type("股票交易严重异常波动公告", body),
            ("10日严重异动", "severe-10d"),
        )

    def test_monitor_rule_version_requires_refresh_after_direction_change(self):
        self.assertIn("zts-strict-risk", MONITOR_RULE_VERSION)

    def test_zts_announcement_is_not_rule_reset_source(self):
        self.assertEqual(
            self.service._monitor_period_dates("2026-10-16", "zts-risk-alert", "ordinary"),
            self.service._monitor_period_dates("2026-10-17", "issuer-disclosure", "ordinary"),
        )

    def test_broker_alert_requires_security_monitoring_wording(self):
        self.assertFalse(OfficialMonitorService._is_accepted_broker_alert("关于股票交易风险提示的公告", "将视情况从重采取被列为重点监控账户措施"))
        self.assertFalse(OfficialMonitorService._is_accepted_broker_alert("关于股票交易风险提示的公告", "交易所已将证券列为重点监控证券"))
        self.assertTrue(OfficialMonitorService._is_accepted_broker_alert("关于股票交易风险提示的公告", "交易所将对以上证券的异常交易行为进行从严认定"))

    def test_semantic_threshold_variants_are_supported(self):
        self.assertEqual(OfficialMonitorService._monitor_type("风险提示", "10 个交易日内涨幅偏离值累计超过100％以上"), ("10日严重异动", "severe-10d"))
        self.assertEqual(OfficialMonitorService._monitor_type("风险提示", "30个交易日内日收盘价格涨幅偏离值累计达到200.15%"), ("30日严重异动", "severe-30d"))

    def test_actual_short_severe_windows_are_supported_for_shanghai_and_shenzhen(self):
        self.assertEqual(
            OfficialMonitorService._monitor_type(
                "关于股票交易风险提示的公告",
                "股票自2026年9月23日至2026年10月8日连续6个交易日收盘价格涨幅偏离值累计超过100%，属于股票交易严重异常波动情形。",
            ),
            ("10日严重异动", "severe-10d"),
        )
        self.assertEqual(
            OfficialMonitorService._monitor_type(
                "关于股票交易风险提示的公告",
                "连续8个交易日内日收盘价格涨幅偏离值累计达到117.06%，属于股票交易严重异常波动的情形。",
            ),
            ("10日严重异动", "severe-10d"),
        )
        self.assertEqual(
            OfficialMonitorService._monitor_type(
                "关于股票交易异常波动的公告",
                "连续3个交易日内收盘价格涨幅偏离值累计超过30%，属于股票交易异常波动。",
            ),
            ("", ""),
        )

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
                "title": "股票交易严重异常波动公告", "date": "2026-10-09", "stockCode": "000993",
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
            item = response["items"][0]
            self.assertEqual(item["sourceType"], "issuer-disclosure")
            self.assertEqual(item["sourceRole"], "issuer_disclosure_backup")
            self.assertEqual(item["confirmationStatus"], "issuer-disclosure-confirmed")
            self.assertEqual(item["riskTone"], "severe-10d")
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
