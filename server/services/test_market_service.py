import unittest
from datetime import datetime
from types import SimpleNamespace

from server.services.market_calc_repository import MarketCalcRepository
from server.services.market_service import MarketService
from server.core.abnormal_rules import BOARD_RULES, Board


class MarketVectorTests(unittest.TestCase):
    """最小收益率向量计算回归测试。"""

    def setUp(self):
        self.service = MarketService()

    def tearDown(self):
        self.service.close()

    def test_compound_return_uses_composite_formula(self):
        self.assertAlmostEqual(MarketService._compound([10.0, 10.0]), 21.0)

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

    def test_prediction_checks_ten_day_line_for_next_day_scope(self):
        """次日预测不能只检查 30 日线，10 日接近 +100% 也必须入选。"""
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 7.0 if index >= 20 else 0.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"001216.SZ": {
                "ts_code": "001216.SZ", "latest_close": 28.04,
                "as_of_trade_date": "20260928", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"000001.SH": {
                "index_code": "000001.SH", "latest_close": 11.3,
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "001216.SZ", "symbol": "001216", "name": "华瓷股份",
            "market": "SSE", "board": "主板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("next_day")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ts_code"], "001216.SZ")
        self.assertTrue(items[0]["deviation"].startswith("10日 "))

    def test_prediction_emits_separate_cards_for_ten_and_thirty_day_lines(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 6.0 if index < 20 else 8.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0,
                "as_of_trade_date": "20260928", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399001.SZ": {
                "index_code": "399001.SZ", "latest_close": 10.0,
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("today")
        by_window = {item["predictionWindow"]: item for item in items}
        self.assertEqual(sorted(by_window), [10, 30])
        self.assertEqual(by_window[10]["rule"], "连续10个交易日内偏离值达到 +100%")
        self.assertEqual(by_window[30]["rule"], "连续30个交易日内偏离值达到 +200%")

    def test_best_window_can_be_shorter_than_rule_horizon(self):
        # 10 日规则允许因有效数据边界少 1～2 日，但不能退化成任意 3 日窗口。
        stock = [{"date": f"2026-09-{index:02d}", "return": value} for index, value in enumerate([0, 0, 0, 0, 0, 0, 0, 10], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 10)
        self.assertEqual(window, 8)
        self.assertAlmostEqual(value, 10.0)

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
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 6.0 if index >= 21 else 0.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"301190.SZ": {
                "ts_code": "301190.SZ", "latest_close": 29.94,
                "as_of_trade_date": "20260929", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399006.SZ": {
                "index_code": "399006.SZ", "latest_close": 100.0,
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "301190.SZ", "symbol": "301190", "name": "善水科技",
            "market": "SZSE", "board": "创业板", "isST": False,
        }]
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        items = service._compute_prediction_from_repository("next_day")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ts_code"], "301190.SZ")
        self.assertTrue(items[0]["deviation"].startswith("9日 "))

    def test_ten_day_rule_never_uses_a_three_day_spike(self):
        stock = [{"date": f"2026-09-{index:02d}", "return": value} for index, value in enumerate([30, 30, 30], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 10)
        self.assertIsNone(value)
        self.assertIsNone(window)

    def test_prediction_excludes_downward_direction(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": -7.0 if index >= 20 else 0.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0,
                "as_of_trade_date": "20260928", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399001.SZ": {
                "index_code": "399001.SZ", "latest_close": 10.0,
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
                "as_of_trade_date": "20260928", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399001.SZ": {
                "index_code": "399001.SZ", "latest_close": 10.0,
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        self.assertEqual(service._compute_prediction_from_repository("next_day"), [])

    def test_today_prediction_only_contains_already_triggered_upward_records(self):
        service = self.service
        dates = [f"2026-08-{index:02d}" for index in range(1, 31)]
        stock_vector = [{"date": day, "return": 2.0 if index >= 20 else 0.0} for index, day in enumerate(dates)]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {"000001.SZ": {
                "ts_code": "000001.SZ", "latest_close": 10.0,
                "as_of_trade_date": "20260928", "stock_return_vector": stock_vector,
            }},
            all_indexes=lambda: {"399001.SZ": {
                "index_code": "399001.SZ", "latest_close": 10.0,
                "index_return_vector": index_vector,
            }},
        )
        service._stock_rows = lambda: [{
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "isST": False,
        }]
        self.assertEqual(service._compute_prediction_from_repository("today"), [])

    def test_intraday_refresh_only_updates_existing_prediction_codes(self):
        service = self.service
        service._calc_repository = SimpleNamespace(
            available=True,
            get_prediction=lambda scope: {"scope": scope, "items": [{"ts_code": "001216.SZ", "deviation": "10日 +95.00%", "currentPrice": "28.00", "change": "+1.00%"}]},
            all_calculations=lambda: {"001216.SZ": {"ts_code": "001216.SZ", "latest_close": 28.0, "stock_return_vector": [{"date": f"2026-09-{index:02d}", "return": 0.0} for index in range(1, 31)]}},
            all_indexes=lambda: {"399001.SZ": {"index_code": "399001.SZ", "latest_close": 10.0, "index_return_vector": [{"date": f"2026-09-{index:02d}", "return": 0.0} for index in range(1, 31)]}},
        )
        service._stock_rows = lambda: [{"ts_code": "001216.SZ", "symbol": "001216", "name": "华瓷股份", "market": "SZSE", "board": "主板", "isST": False}]
        requested = []
        service._realtime = SimpleNamespace(fetch_many=lambda codes: (requested.extend(list(codes)) or {
            "001216.SZ": {"current": 28.2, "pctChg": 0.71, "updatedAt": "2026-09-29T10:00:00", "source": "腾讯行情"},
            "399001.SZ": {"current": 10.1, "pctChg": 1.0, "updatedAt": "2026-09-29T10:00:00", "source": "腾讯行情"},
        }))
        result = service._refresh_existing_prediction_scope("next_day")
        self.assertEqual(["001216.SZ", "399001.SZ"], requested)
        self.assertEqual(result[0]["currentPrice"], "28.20")
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


class PredictionVisibilityTests(unittest.TestCase):
    """次日预测按交易阶段和数据集日期控制可见性。"""

    STATE = {
        "previousTradeDate": "20260930",
        "targetTradeDate": "20261008",
    }

    def test_rest_day_uses_previous_trade_day_dataset(self):
        self.assertTrue(MarketService._next_day_dataset_available(
            "rest_day", self.STATE, "20260930", True,
        ))

    def test_trade_day_pre_open_uses_previous_trade_day_dataset(self):
        self.assertTrue(MarketService._next_day_dataset_available(
            "pre_open", self.STATE, "20260930", True,
        ))

    def test_intraday_hides_next_day_dataset(self):
        self.assertFalse(MarketService._next_day_dataset_available(
            "intraday", self.STATE, "20260930", True,
        ))

    def test_post_close_pending_shows_current_initial_dataset(self):
        self.assertTrue(MarketService._next_day_dataset_available(
            "post_close_pending", self.STATE, "20261008", True, "initial",
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
            "post_close_confirmed", self.STATE, "20261008", True, "formal",
        ))

    def test_missing_dataset_is_never_available(self):
        self.assertFalse(MarketService._next_day_dataset_available(
            "rest_day", self.STATE, "20260930", False,
        ))


if __name__ == "__main__":
    unittest.main()
