import unittest
from datetime import datetime
from types import SimpleNamespace

from server.services.market_calc_repository import MarketCalcRepository
from server.services.market_service import MarketService, _index_code_for_stock
from server.core.abnormal_rules import BOARD_RULES, Board


class MarketVectorTests(unittest.TestCase):
    """最小收益率向量计算回归测试。"""

    def setUp(self):
        self.service = MarketService()

    def tearDown(self):
        self.service.close()

    def test_compound_return_uses_composite_formula(self):
        self.assertAlmostEqual(MarketService._compound([10.0, 10.0]), 21.0)

    def test_required_up_percent_today_solves_current_day_not_next_day(self):
        """当日卡的触发涨幅必须把最新股票日收益作为未知量。"""
        stock = [{"date": f"2026-10-{index:02d}", "return": value} for index, value in enumerate([10.0, 10.0, 5.0], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        # 目标 +30%，前两日股票累计 +21%，当日需要约 7.44%。
        self.assertAlmostEqual(
            MarketService._required_up_percent(stock, index, 3, 30.0, forecast_scope="today"),
            7.4380165,
            places=5,
        )
        # 次日口径使用完整当前窗口；当前已为 +26.05%，只需约 2.32% 追加到 +30%。
        self.assertAlmostEqual(
            MarketService._required_up_percent(stock, index, 3, 30.0, forecast_scope="next_day"),
            2.3219205,
            places=5,
        )

    def test_force_history_rebuild_is_explicitly_supported(self):
        service = self.service
        calls = []
        service._latest_completed_trade_date = lambda: "20261009"
        service.update_market_data = lambda **kwargs: calls.append(kwargs) or {"updated": False, "reason": "test"}
        result = service.rebuild_formal_history()
        self.assertFalse(result["updated"])
        self.assertEqual(calls, [{"data_stage": "formal", "allow_non_trade_day": True, "force_history": True, "history_end_date": "20261009"}])

    def test_rule_upgrade_uses_one_completed_history_rebuild(self):
        service = self.service
        service._calc_repository = SimpleNamespace(
            available=True,
            get_prediction=lambda _scope: {"rule_version": "old-version"},
            all_calculations=lambda: {"000001.SZ": {"as_of_trade_date": "20261013"}},
            all_indexes=lambda: {"399107.SZ": {"as_of_trade_date": "20261013"}},
        )
        calls = []
        service.rebuild_formal_history = lambda: calls.append("formal-history") or {"updated": True}

        service._rebuild_predictions_if_rule_changed()

        self.assertEqual(calls, ["formal-history"])

    def test_failed_rule_upgrade_does_not_relabel_old_formal_vectors(self):
        service = self.service
        service._calc_repository = SimpleNamespace(
            available=True,
            get_prediction=lambda _scope: {"rule_version": "old-version"},
        )
        service.rebuild_formal_history = lambda: {"updated": False, "reason": "incomplete upstream data"}
        service._latest_data_stage_date = lambda: ("20261013", "formal")
        generated = []
        service._generate_prediction_datasets = lambda stage: generated.append(stage)

        service._rebuild_predictions_if_rule_changed()

        self.assertEqual(generated, [])

    def test_deviation_aligns_same_trade_dates(self):
        stock = [
            {"date": "2026-09-25", "return": 10.0},
            {"date": "2026-09-28", "return": 0.0},
        ]
        index = [
            {"date": "2026-09-25", "return": 2.0},
            {"date": "2026-09-28", "return": 0.0},
        ]
        value = MarketService._vector_deviation(stock, index, 2)
        self.assertAlmostEqual(value, 8.0)

    def test_quote_replaces_same_day_return(self):
        entries = [
            {"date": "2026-09-25", "return": 5.0},
            {"date": "2026-09-28", "return": 3.0},
        ]
        result = MarketService._apply_quote_to_vector(
            entries,
            108.15,
            {"current": 109.23, "updatedAt": "2026-09-28T10:00:00"},
        )
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[-1]["return"], 4.0285714286, places=6)

    def test_iso_quote_date_appends_compact_trade_date_using_preclose(self):
        entries = [{"date": "20261012", "return": 9.98}]
        result = MarketService._apply_quote_to_vector(
            entries,
            109.98,
            {"current": 106.19, "preClose": 100.0, "updatedAt": "2026-10-13T16:14:30"},
        )
        self.assertEqual(result[-1]["date"], "20261013")
        self.assertAlmostEqual(result[-1]["return"], 6.19, places=6)

    def test_iso_quote_date_replaces_same_compact_trade_date(self):
        entries = [
            {"date": "20261012", "return": 2.0},
            {"date": "20261013", "return": 9.98},
        ]
        result = MarketService._apply_quote_to_vector(
            entries,
            109.98,
            {"current": 106.19, "preClose": 100.0, "updatedAt": "2026-10-13T16:14:30"},
        )
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[-1]["return"], 6.19, places=6)

    def test_live_quotes_must_share_source_date_and_nearby_time(self):
        stock = {"source": "腾讯行情", "updatedAt": "2026-10-13T14:55:00"}
        same_snapshot = {"source": "腾讯行情", "updatedAt": "2026-10-13T14:59:59"}
        different_time = {"source": "腾讯行情", "updatedAt": "2026-10-13T14:40:00"}
        different_source = {"source": "新浪行情", "updatedAt": "2026-10-13T14:55:00"}
        different_day = {"source": "腾讯行情", "updatedAt": "2026-10-12T14:55:00"}
        self.assertTrue(MarketService._quotes_share_snapshot(stock, same_snapshot))
        self.assertFalse(MarketService._quotes_share_snapshot(stock, different_time))
        self.assertFalse(MarketService._quotes_share_snapshot(stock, different_source))
        self.assertFalse(MarketService._quotes_share_snapshot(stock, different_day))
        self.assertFalse(MarketService._quotes_share_snapshot(stock, {"source": "腾讯行情"}))

    def test_detail_quote_and_calculation_vectors_share_current_trade_date(self):
        service = self.service
        service._calc_repository = SimpleNamespace(get_index=lambda _code: {
            "index_code": "399107.SZ",
            "latest_close": 100.0,
            "as_of_trade_date": "20261012",
            "index_return_vector": [
                {"date": "20261011", "return": 0.0},
                {"date": "20261012", "return": 0.0},
            ],
        })
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {
            "000001.SZ": {"current": 106.19, "preClose": 100.0, "pctChg": 6.19, "updatedAt": "2026-10-13T16:14:30"},
            "399107.SZ": {"current": 101.0, "preClose": 100.0, "pctChg": 1.0, "updatedAt": "2026-10-13T16:14:30"},
        })
        service._future_trade_dates = lambda *_args: []
        stock = {"ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票", "market": "SZSE", "board": "主板", "isST": False}
        calc = {
            "latest_close": 100.0,
            "as_of_trade_date": "20261012",
            "data_stage": "formal",
            "stock_return_vector": [
                {"date": "20261011", "return": 0.0},
                {"date": "20261012", "return": 0.0},
            ],
        }
        result = service._detail_from_repository(stock, calc)
        self.assertEqual(result["change"], "+6.19%")
        self.assertEqual(result["calculationInput"]["stock"][-1]["date"], "20261013")
        self.assertEqual(result["calculationInput"]["index"][-1]["date"], "20261013")
        self.assertAlmostEqual(result["calculationInput"]["stock"][-1]["return"], 6.19, places=6)

    def test_detail_does_not_mix_unpaired_realtime_quotes_into_calculation(self):
        service = self.service
        service._calc_repository = SimpleNamespace(get_index=lambda _code: {
            "index_code": "399107.SZ", "latest_close": 100.0, "as_of_trade_date": "20261012",
            "index_return_vector": [{"date": "20261011", "return": 0.0}, {"date": "20261012", "return": 0.0}],
        })
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {
            "000001.SZ": {"current": 106.19, "preClose": 100.0, "pctChg": 6.19, "source": "腾讯行情", "updatedAt": "2026-10-13T16:14:30"},
            "399107.SZ": {"current": 101.0, "preClose": 100.0, "pctChg": 1.0, "source": "新浪行情", "updatedAt": "2026-10-13T16:14:30"},
        })
        service._future_trade_dates = lambda *_args: []
        stock = {"ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票", "market": "SZSE", "board": "主板", "isST": False}
        calc = {
            "latest_close": 100.0, "as_of_trade_date": "20261012", "data_stage": "formal",
            "stock_return_vector": [{"date": "20261011", "return": 0.0}, {"date": "20261012", "return": 0.0}],
        }
        result = service._detail_from_repository(stock, calc)
        self.assertEqual(result["change"], "+0.00%")
        self.assertEqual(result["calculationInput"]["stock"][-1]["date"], "20261012")
        self.assertEqual(result["dataQuality"]["source"], "CloudBase MySQL")

    def test_prediction_excludes_stock_and_index_with_different_snapshot_dates(self):
        service = self.service
        vector = [{"date": f"2026-10-{day:02d}", "return": 8.0 if day >= 3 else 0.0} for day in range(1, 13)]
        service._calc_repository = SimpleNamespace(
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0, "as_of_trade_date": "20261012",
                "stock_return_vector": vector,
            }},
            all_indexes=lambda: {"399107.SZ": {
                "index_code": "399107.SZ", "latest_close": 10.0, "as_of_trade_date": "20261011",
                "index_return_vector": vector[:-1],
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        items = service._compute_prediction_from_repository("today")
        self.assertEqual(items, [])
        self.assertEqual(service._prediction_generation_stats["today"]["excludedByDataQuality"], 1)

    def test_formal_backfill_replaces_same_day_initial_return_with_pct_chg(self):
        calculations = {
            "000001.SZ": {
                "latest_close": 110.0,
                "as_of_trade_date": "20260930",
                "stock_return_vector": [{"date": "20260930", "return": 5.0}],
            }
        }

        class Frame:
            empty = False

            @staticmethod
            def to_dict(_orient):
                return [{"ts_code": "000001.SZ", "trade_date": "20260930", "close": 120.0, "pct_chg": 20.0}]

        self.service._append_daily_rows(calculations, {"20260930": Frame()}, ["20260930"])
        self.assertEqual(calculations["000001.SZ"]["stock_return_vector"][-1]["return"], 20.0)

    def test_same_direction_counts_one_continuous_run_once(self):
        """重叠的 3 日异常窗口只消费一次，不重复计数。"""
        dates = [f"2026-08-{index:02d}" for index in range(1, 11)]
        stock = [{"date": day, "return": 10.0 if 3 <= index <= 5 else 0.0} for index, day in enumerate(dates, 1)]
        index = [{"date": day, "return": 0.0} for day in dates]
        metrics = MarketService._vector_metrics(stock, index, BOARD_RULES[Board.MAIN])
        self.assertEqual(metrics["up"], 1)
        self.assertEqual(metrics["down"], 0)

    def test_stock_name_initials_are_recomputed_from_name(self):
        row = MarketService._normalise_stock_row({
            "ts_code": "002975.SZ", "symbol": "002975", "name": "博杰股份",
            "exchange": "SZSE", "name_initials": "WRONG",
        })
        self.assertEqual(row["nameInitials"], "BJGF")

    def test_normalise_stock_derives_price_limit_for_known_active_board(self):
        row = MarketService._normalise_stock_row({
            "ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行",
            "market": "主板", "exchange": "SZSE", "list_status": "L",
        })
        self.assertTrue(row["priceLimitEnabled"])

    def test_normalise_stock_keeps_unknown_exchange_conservative(self):
        row = MarketService._normalise_stock_row({
            "ts_code": "X00001", "symbol": "000001", "name": "未知股票",
            "market": "OTHER", "exchange": "OTHER", "list_status": "L",
        })
        self.assertIsNone(row["priceLimitEnabled"])

    def test_prediction_checks_ten_day_line_for_next_day_scope(self):
        """次日预测不能只检查 30 日线，10 日接近 +100% 也必须入选。"""
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 8.0 if index >= 20 else 0.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"001216.SZ": {
                "ts_code": "001216.SZ", "latest_close": 28.04,
                "as_of_trade_date": "20260830", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"000001.SH": {
                "index_code": "000001.SH", "latest_close": 11.3, "as_of_trade_date": "20260830",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "001216.SZ", "symbol": "001216", "name": "华瓷股份",
            "market": "SSE", "board": "主板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("next_day")
        self.assertEqual({item["ruleKey"] for item in items}, {"severe_10d"})
        self.assertTrue(any(item["deviation"].startswith("9日 ") for item in items))

    def test_prediction_emits_separate_cards_for_ten_and_thirty_day_lines(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 6.0 if index < 20 else 9.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0,
                "as_of_trade_date": "20260830", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399107.SZ": {
                "index_code": "399107.SZ", "latest_close": 10.0, "as_of_trade_date": "20260830",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("today")
        by_rule = {item["ruleKey"]: item for item in items}
        self.assertEqual(sorted(by_rule), ["severe_10d", "severe_30d"])
        self.assertTrue(by_rule["severe_10d"]["triggerCondition"].startswith("涨幅达 "))
        self.assertTrue(by_rule["severe_30d"]["triggerCondition"].startswith("涨幅达 "))
        self.assertTrue(by_rule["severe_10d"]["triggered"])
        self.assertEqual(by_rule["severe_10d"]["alertText"], "已触发")
        self.assertTrue(by_rule["severe_10d"]["triggerValue"].startswith("+"))

    def test_today_prediction_drops_unreachable_pending_thirty_day_card(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 0.0 if index < 20 else 8.5} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0,
                "as_of_trade_date": "20260830", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399107.SZ": {
                "index_code": "399107.SZ", "latest_close": 10.0, "as_of_trade_date": "20260830",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("today")
        by_rule = {item["ruleKey"]: item for item in items}
        self.assertIn("severe_10d", by_rule)
        self.assertTrue(by_rule["severe_10d"]["triggered"])
        self.assertNotIn("severe_30d", by_rule)

    def test_same_direction_is_a_prediction_card(self):
        service = self.service
        vector = [{"date": f"2026-09-{index:02d}", "return": 0.0} for index in range(1, 11)]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {"ts_code": "000001.SZ", "latest_close": 10.0, "as_of_trade_date": "20260910", "stock_return_vector": vector}},
            all_indexes=lambda: {"399107.SZ": {"index_code": "399107.SZ", "latest_close": 10.0, "as_of_trade_date": "20260910", "index_return_vector": vector}},
        )
        service._stock_rows = lambda: [{"ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票", "market": "SZSE", "board": "主板", "isST": False}]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        service._vector_metrics = lambda *_args: {"deviations": {3: None, 10: 0.0, 30: None}, "window_lengths": {3: None, 10: 10, 30: None}, "up": 4, "down": 0}
        items = service._compute_prediction_from_repository("today")
        self.assertFalse(any(item["ruleKey"] == "same_direction_10d" for item in items))

    def test_monitored_stock_adds_short_window_prediction_cards(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 11)]
        stock_vector = [{"date": day, "return": 8.0 if index >= 8 else 0.0} for index, day in enumerate(dates, 1)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {"ts_code": "000001.SZ", "latest_close": 10.0, "as_of_trade_date": "20260810", "stock_return_vector": stock_vector}},
            all_indexes=lambda: {"399107.SZ": {"index_code": "399107.SZ", "latest_close": 10.0, "as_of_trade_date": "20260810", "index_return_vector": index_vector}},
        )
        service._stock_rows = lambda: [{"ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票", "market": "SZSE", "board": "主板", "isST": False}]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        service.set_monitor_provider(lambda: {"items": [{"symbol": "000001", "riskTone": "severe-10d"}]})
        keys = {item["ruleKey"] for item in service._compute_prediction_from_repository("today")}
        self.assertIn("ordinary_3d", keys)
        self.assertIn("same_direction_10d", keys)

    def test_same_dataset_merge_deduplicates_by_rule_and_target_date(self):
        merged = MarketService._merge_prediction_items(
            [{"predictionKey": "688137.SH-severe_10d-20261008", "triggered": True}],
            [
                {"predictionKey": "688137.SH-severe_30d-20261008", "triggered": False},
                {"predictionKey": "688137.SH-severe_10d-20261008", "triggered": False},
                {"predictionKey": "301190.SZ-severe_10d-20261008", "triggered": False},
            ],
        )
        self.assertEqual([item["predictionKey"] for item in merged], [
            "688137.SH-severe_10d-20261008", "688137.SH-severe_30d-20261008", "301190.SZ-severe_10d-20261008",
        ])

    def test_legacy_trigger_text_is_not_rewritten(self):
        merged = MarketService._merge_prediction_items(
            [{"predictionKey": "301190.SZ-10d", "trigger": "已达到阈值", "triggered": True}],
            [],
        )
        self.assertEqual(merged[0]["trigger"], "已达到阈值")

    def test_best_window_can_be_shorter_than_rule_horizon(self):
        # 历史不足 10 日时，在全部可用尾部区间中选择最大上涨偏离。
        stock = [{"date": f"2026-09-{index:02d}", "return": value} for index, value in enumerate([0, 0, 0, 0, 0, 0, 0, 10], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 10)
        self.assertEqual(window, 1)
        self.assertAlmostEqual(value, 10.0)

    def test_ten_day_window_uses_maximum_tail_interval(self):
        # 10 日规则允许在最近 10 个交易日内选择最大上涨区间。
        stock = [{"date": f"2026-10-{index:02d}", "return": value} for index, value in enumerate([0, 0, 0, 0, 0, 0, 10], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 10)
        self.assertEqual(window, 1)
        self.assertAlmostEqual(value, 10.0)

    def test_severe_card_is_hidden_when_same_rule_is_in_monitor(self):
        service = self.service
        service.set_monitor_provider(lambda: {"items": [{"symbol": "301190", "riskTone": "severe-10d"}]})
        monitored = service._monitored_rule_codes()
        severe = {"ts_code": "301190.SZ", "symbol": "301190", "ruleKey": "severe_10d"}
        ordinary = {"ts_code": "301190.SZ", "symbol": "301190", "ruleKey": "ordinary_3d"}
        self.assertFalse(service._prediction_item_allowed(severe, monitored, "today"))
        self.assertTrue(service._prediction_item_allowed(ordinary, monitored, "today"))

    def test_seven_valid_days_can_trigger_ten_day_prediction(self):
        stock = [{"date": f"2026-09-{index:02d}", "return": 0.0 if index < 7 else 100.0} for index in range(1, 8)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 10)
        self.assertEqual(window, 1)
        self.assertGreaterEqual(value, 100.0)

    def test_fixed_window_uses_latest_sessions_not_best_historical_subwindow(self):
        stock = [{"date": f"2026-09-{index:02d}", "return": value} for index, value in enumerate([80.0, 0.0, 0.0, 0.0, 0.0], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 3)
        self.assertEqual(window, 3)
        self.assertAlmostEqual(value, 0.0)

    def test_index_mapping_uses_market_benchmarks(self):
        self.assertEqual("399107.SZ", _index_code_for_stock({"market": "SZSE", "board": "主板"}))
        self.assertEqual("399102.SZ", _index_code_for_stock({"market": "SZSE", "board": "创业板"}))

    def test_expired_monitor_snapshot_does_not_grant_short_card_eligibility(self):
        service = self.service
        service.set_monitor_provider(lambda: {"items": [{
            "symbol": "000001", "riskTone": "severe-10d",
            "monitorDates": ["2026-09-01", "2026-09-02"], "monitorEndDate": "2026-09-02",
        }]})
        self.assertNotIn("__severe_monitor__", service._monitored_rule_codes())

    def test_today_prediction_accepts_seven_valid_days_at_ten_day_threshold(self):
        service = self.service
        dates = [f"2026-09-{index:02d}" for index in range(1, 8)]
        stock_vector = [{"date": day, "return": 12.0} for day in dates]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"600825.SH": {
                "ts_code": "600825.SH", "latest_close": 10.0,
                "as_of_trade_date": "20260907", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"000001.SH": {
                "index_code": "000001.SH", "latest_close": 100.0, "as_of_trade_date": "20260907",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "600825.SH", "symbol": "600825", "name": "新华传媒",
            "market": "SSE", "board": "主板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("today")
        severe = [item for item in items if item["ruleKey"] == "severe_10d"]
        self.assertEqual(len(severe), 1)
        self.assertTrue(severe[0]["triggered"])
        self.assertTrue(severe[0]["deviation"].startswith("7日 "))

    def test_suspension_reset_discards_pre_resume_returns(self):
        """复牌后只从首个有效交易日重新累计，不能把停牌前涨幅带入。"""
        index = [{"date": day, "return": 0.0} for day in ("2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25")]
        stock = [
            {"date": "2026-09-21", "return": 80.0},
            {"date": "2026-09-22", "return": 10.0},
            {"date": "2026-09-25", "return": 5.0},
        ]
        trimmed_stock, trimmed_index, meta = MarketService._trim_vectors_after_suspension(stock, index)
        self.assertEqual([item["date"] for item in trimmed_stock], ["2026-09-25"])
        self.assertEqual([item["date"] for item in trimmed_index], ["2026-09-25"])
        self.assertEqual(meta["resetDate"], "20260925")
        self.assertFalse(meta["suspended"])

    def test_tail_suspension_is_not_predicted(self):
        """最新交易日仍停牌时不能把停牌前结果伪装成次日候选。"""
        stock = [{"date": day, "return": 5.0} for day in ("2026-09-21", "2026-09-22")]
        index = [{"date": day, "return": 0.0} for day in ("2026-09-21", "2026-09-22", "2026-09-23")]
        _, _, meta = MarketService._trim_vectors_after_suspension(stock, index)
        self.assertTrue(meta["suspended"])

    def test_prediction_accepts_nine_valid_days_for_ten_day_line(self):
        """10 日规则在有效窗口边界允许 8/9/10 日，善水科技同类情形应入选。"""
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(22, 31)]
        stock_vector = [{"date": day, "return": 6.0} for day in dates]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"301190.SZ": {
                "ts_code": "301190.SZ", "latest_close": 29.94,
                "as_of_trade_date": "20260830", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399102.SZ": {
                "index_code": "399102.SZ", "latest_close": 100.0, "as_of_trade_date": "20260830",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "301190.SZ", "symbol": "301190", "name": "善水科技",
            "market": "SZSE", "board": "创业板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("next_day")
        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(items[0]["ts_code"], "301190.SZ")
        self.assertTrue(items[0]["deviation"].startswith("9日 "))

    def test_today_prediction_includes_shanshui_after_ten_day_trigger(self):
        """善水科技触发 10 日 +100% 后，当日快照不能被次日过滤逻辑漏掉。"""
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 30)]
        # 末尾 9 个有效交易日累计相对指数偏离超过 +100%，
        # 与实际善水科技的边界形态一致；30 日线仍未达到 +200%。
        stock_returns = [0.0] * 19 + [12.0] * 10
        stock_vector = [{"date": day, "return": value} for day, value in zip(dates, stock_returns)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"301190.SZ": {
                "ts_code": "301190.SZ", "latest_close": 35.93,
                "as_of_trade_date": "20260829", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399102.SZ": {
                "index_code": "399102.SZ", "latest_close": 3210.0, "as_of_trade_date": "20260829",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "301190.SZ", "symbol": "301190", "name": "善水科技",
            "market": "SZSE", "board": "创业板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("today")
        self.assertGreaterEqual(len(items), 1)
        self.assertEqual(items[0]["ts_code"], "301190.SZ")
        severe_10 = next(item for item in items if item["ruleKey"] == "severe_10d")
        self.assertEqual(severe_10["predictionWindow"], 10)
        self.assertTrue(severe_10["triggered"])

    def test_ten_day_rule_never_uses_a_three_day_spike(self):
        stock = [{"date": f"2026-09-{index:02d}", "return": value} for index, value in enumerate([30, 30, 30], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 10)
        self.assertEqual(window, 3)
        self.assertAlmostEqual(value, 119.7, places=1)

    def test_effective_windows_match_next_day_examples(self):
        ten_day_stock = [{"date": f"2026-09-{index:02d}", "return": value} for index, value in enumerate([0.0] + [5.0] * 9, 1)]
        ten_day_index = [{"date": item["date"], "return": 0.0} for item in ten_day_stock]
        _, ten_window = MarketService._best_vector_deviation(ten_day_stock, ten_day_index, 10)
        self.assertEqual(ten_window, 9)

        thirty_day_stock = [{"date": f"2026-08-{index:02d}", "return": value} for index, value in enumerate([0.0] * 8 + [2.0] * 22, 1)]
        thirty_day_index = [{"date": item["date"], "return": 0.0} for item in thirty_day_stock]
        _, thirty_window = MarketService._best_vector_deviation(thirty_day_stock, thirty_day_index, 30)
        self.assertEqual(thirty_window, 22)

    def test_next_day_excludes_stock_already_triggered_on_any_severe_rule(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 10.0} for day in dates]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"301190.SZ": {
                "ts_code": "301190.SZ", "latest_close": 35.93,
                "as_of_trade_date": "20260830", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399102.SZ": {
                "index_code": "399102.SZ", "latest_close": 3210.0,
                "as_of_trade_date": "20260830", "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "301190.SZ", "symbol": "301190", "name": "善水科技",
            "market": "SZSE", "board": "创业板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        self.assertEqual(service._compute_prediction_from_repository("next_day"), [])

    def test_prediction_sort_prioritizes_window_then_trigger_distance(self):
        items = [
            {"ruleKey": "severe_10d", "triggerValue": "+3.02%", "ts_code": "002242.SZ"},
            {"ruleKey": "severe_30d", "triggerValue": "+15.73%", "ts_code": "688137.SH"},
            {"ruleKey": "severe_10d", "triggerValue": "+2.07%", "ts_code": "600241.SH"},
            {"ruleKey": "ordinary_3d", "triggerValue": "+1.00%", "ts_code": "000001.SZ"},
        ]
        ordered = MarketService._sort_prediction_items(items)
        self.assertEqual([item["ts_code"] for item in ordered], ["688137.SH", "600241.SH", "002242.SZ", "000001.SZ"])

    def test_prediction_excludes_downward_direction(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": -7.0 if index >= 20 else 0.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0,
                "as_of_trade_date": "20260830", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399107.SZ": {
                "index_code": "399107.SZ", "latest_close": 10.0, "as_of_trade_date": "20260830",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        self.assertEqual(service._compute_prediction_from_repository("today"), [])

    def test_prediction_uses_board_limit_instead_of_fixed_twenty_percent_window(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        # 最优有效区间距离 +100% 仍超过主板单日 +10% 上限。
        stock_vector = [{"date": day, "return": 5.0 if index >= 20 else 0.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0,
                "as_of_trade_date": "20260830", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399107.SZ": {
                "index_code": "399107.SZ", "latest_close": 10.0, "as_of_trade_date": "20260830",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        self.assertFalse(any(item["ruleKey"] == "severe_10d" for item in service._compute_prediction_from_repository("next_day")))

    def test_today_prediction_includes_nearby_untriggered_upward_candidates(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 6.8 if index >= 20 else 0.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0,
                "as_of_trade_date": "20260830", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399107.SZ": {
                "index_code": "399107.SZ", "latest_close": 10.0, "as_of_trade_date": "20260830",
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        items = service._compute_prediction_from_repository("today")
        self.assertNotIn("ordinary_3d", {item["ruleKey"] for item in items})

    def test_intraday_refresh_only_updates_existing_prediction_codes(self):
        service = self.service
        service._calc_repository = SimpleNamespace(
            available=True,
            get_prediction=lambda scope: {"scope": scope, "items": [{"ts_code": "001216.SZ", "ruleKey": "severe_10d", "predictionWindow": 10, "scope": "次日", "deviation": "10日 +95.00%", "currentPrice": "28.00", "change": "+1.00%", "trigger": "10日偏离达到 +100.00%", "triggerCondition": "10日偏离达到 +100.00%", "triggerValue": "+100.00%", "triggered": True, "cardTone": "triggered"}]},
            all_calculations=lambda: {"001216.SZ": {"ts_code": "001216.SZ", "latest_close": 28.0, "stock_return_vector": [{"date": f"2026-09-{index:02d}", "return": 0.0} for index in range(1, 31)]}},
            all_indexes=lambda: {"399107.SZ": {"index_code": "399107.SZ", "latest_close": 10.0, "index_return_vector": [{"date": f"2026-09-{index:02d}", "return": 0.0} for index in range(1, 31)]}},
        )
        service._stock_rows = lambda: [{"ts_code": "001216.SZ", "symbol": "001216", "name": "华瓷股份", "market": "SZSE", "board": "主板", "isST": False}]
        requested = []
        quote_state = {"current": 28.2}
        service._realtime = SimpleNamespace(fetch_many=lambda codes: (requested.extend(list(codes)) or {
            "001216.SZ": {"current": quote_state["current"], "pctChg": 0.71, "updatedAt": "2026-09-30T10:00:00", "source": "腾讯行情"},
            "399107.SZ": {"current": 10.1, "pctChg": 1.0, "updatedAt": "2026-09-30T10:00:00", "source": "腾讯行情"},
        }))
        result = service._refresh_existing_prediction_scope("next_day")
        self.assertEqual(["001216.SZ", "399107.SZ"], requested)
        self.assertNotIn("currentPrice", result[0])
        self.assertFalse(result[0]["triggered"])
        self.assertEqual(result[0]["cardTone"], "")
        self.assertEqual(result[0]["trigger"], result[0]["triggerCondition"])
        self.assertNotEqual(result[0]["deviation"], "10日 +95.00%")
        self.assertTrue(result[0]["deviation"].endswith("-0.29%"))
        today_result = service._refresh_existing_prediction_scope("next_day", "today")
        self.assertEqual(today_result[0]["scope"], "当日")
        self.assertNotEqual(today_result[0]["triggerValue"], result[0]["triggerValue"])
        quote_state["current"] = 60.0
        recovered = service._refresh_existing_prediction_scope("next_day")
        self.assertTrue(recovered[0]["triggered"])
        self.assertEqual(recovered[0]["cardTone"], "triggered")
        self.assertEqual(recovered[0]["trigger"], recovered[0]["triggerCondition"])
        self.assertFalse(hasattr(service._calc_repository, "save_prediction"))


class MarketRepositoryConfigTests(unittest.TestCase):
    """未配置 MySQL 时必须保持本地开发可用，不触发网络或数据库连接。"""

    def test_repository_is_disabled_without_mysql_config(self):
        repository = MarketCalcRepository({"host": "", "user": "", "database": ""})
        self.assertFalse(repository.available)
        self.assertIsNone(repository.list_stocks())
        self.assertIsNone(repository.all_calculations())

    def test_formal_retry_windows_skip_non_slot_minutes(self):
        self.assertTrue(MarketService._formal_retry_due(datetime(2026, 9, 30, 16, 0)))
        self.assertTrue(MarketService._formal_retry_due(datetime(2026, 9, 30, 17, 30)))
        self.assertTrue(MarketService._formal_retry_due(datetime(2026, 9, 30, 18, 0)))
        self.assertTrue(MarketService._formal_retry_due(datetime(2026, 9, 30, 23, 0)))
        self.assertFalse(MarketService._formal_retry_due(datetime(2026, 9, 30, 16, 15)))
        self.assertFalse(MarketService._formal_retry_due(datetime(2026, 9, 30, 23, 30)))

    def test_cold_start_catch_up_is_limited_to_first_half_of_allowed_hour(self):
        self.assertTrue(MarketService._formal_catch_up_due(datetime(2026, 9, 30, 16, 15)))
        self.assertTrue(MarketService._formal_catch_up_due(datetime(2026, 9, 30, 23, 30)))
        self.assertFalse(MarketService._formal_catch_up_due(datetime(2026, 9, 30, 23, 31)))
        self.assertFalse(MarketService._formal_catch_up_due(datetime(2026, 9, 30, 15, 45)))

    def test_trade_day_gate_uses_calendar_without_latest_quote_request(self):
        service = MarketService()

        class Calendar:
            @staticmethod
            def to_dict(_orient):
                return [{"cal_date": "20261001", "is_open": 0}, {"cal_date": "20260930", "is_open": 1}]

        calls = []
        service.client = SimpleNamespace(trade_cal=lambda start, end: (calls.append((start, end)) or Calendar()))
        self.assertTrue(service._is_trade_day("20260930"))
        self.assertFalse(service._is_trade_day("20261001"))
        self.assertEqual(calls, [("20260930", "20260930"), ("20261001", "20261001")])
        service.close()

    def test_rest_day_formal_backfill_requires_explicit_opt_in(self):
        self.assertFalse(MarketService._market_update_allowed(False))
        # 显式允许时才会越过自然日闸门；后续行情读取仍由正式数据完整性校验保护。
        self.assertTrue(MarketService._market_update_allowed(False, allow_non_trade_day=True))
        self.assertTrue(MarketService._market_update_allowed(True))

    def test_latest_data_stage_prefers_formal_batch(self):
        service = MarketService()
        service._calc_repository = SimpleNamespace(available=True, all_calculations=lambda: {
            "a": {"as_of_trade_date": "20260930", "data_stage": "initial"},
            "b": {"as_of_trade_date": "20260930", "data_stage": "formal"},
        })
        self.assertEqual(service._latest_data_stage_date(), ("20260930", "formal"))
        service.close()

    def test_formal_failure_refreshes_same_day_initial_snapshot_and_predictions(self):
        service = MarketService()
        service._calc_repository = SimpleNamespace(available=True, all_calculations=lambda: {
            "a": {"as_of_trade_date": "20261013", "data_stage": "initial"},
        })
        calls = []
        service.update_initial_data = lambda: {"updated": True, "tradeDate": "20261013"}
        service._generate_prediction_datasets = lambda stage: calls.append(stage)
        self.assertTrue(service._refresh_initial_snapshot_after_formal_failure("20261013"))
        self.assertEqual(calls, ["initial"])
        service.close()


class PredictionVisibilityTests(unittest.TestCase):
    """次日预测按交易阶段和数据集日期控制可见性。"""

    STATE = {
        "previousTradeDate": "20260930",
        "targetTradeDate": "20261008",
        "nextTradeDate": "20261009",
    }

    def test_rest_day_uses_previous_trade_day_dataset(self):
        self.assertTrue(MarketService._next_day_dataset_available(
            "rest_day", self.STATE, "20261008", True,
        ))

    def test_trade_day_pre_open_uses_previous_trade_day_dataset(self):
        self.assertTrue(MarketService._next_day_dataset_available(
            "pre_open", self.STATE, "20261008", True,
        ))

    def test_dataset_open_pending_hides_dataset_after_today_switch(self):
        self.assertFalse(MarketService._next_day_dataset_available(
            "dataset_open_pending", self.STATE, "20261008", True,
        ))

    def test_intraday_hides_next_day_dataset(self):
        self.assertFalse(MarketService._next_day_dataset_available(
            "intraday", self.STATE, "20260930", True,
        ))

    def test_post_close_pending_shows_current_initial_dataset(self):
        self.assertTrue(MarketService._next_day_dataset_available(
            "post_close_pending", self.STATE, "20261009", True, "initial",
        ))

    def test_post_close_pending_hides_previous_dataset(self):
        self.assertFalse(MarketService._next_day_dataset_available(
            "post_close_pending", self.STATE, "20260930", True, "initial",
        ))

    def test_confirmed_close_requires_current_trade_day_dataset(self):
        self.assertFalse(MarketService._next_day_dataset_available(
            "post_close_confirmed", self.STATE, "20260930", True, "formal",
        ))
        self.assertTrue(MarketService._next_day_dataset_available(
            "post_close_confirmed", self.STATE, "20261009", True, "formal",
        ))

    def test_missing_dataset_is_never_available(self):
        self.assertFalse(MarketService._next_day_dataset_available(
            "rest_day", self.STATE, "20260930", False,
        ))


if __name__ == "__main__":
    unittest.main()
