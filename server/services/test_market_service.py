import unittest
from datetime import datetime
from types import SimpleNamespace

from server.services.market_calc_repository import MarketCalcRepository
from server.services.market_service import CalculationDataUnavailable, MarketService, _index_code_for_stock
from server.core.abnormal_rules import BOARD_RULES, Board


class MarketVectorTests(unittest.TestCase):
    """最小收益率向量计算回归测试。"""

    def setUp(self):
        self.service = MarketService()

    def tearDown(self):
        self.service.close()

    def test_single_stock_detail_fails_instead_of_fetching_daily_when_database_unavailable(self):
        service = self.service
        service._stock = lambda _code: self.fail("must fail before querying stock data")
        service._calc_repository = SimpleNamespace(available=False)
        service._market_context = lambda: self.fail("must not fall back to remote daily data")

        with self.assertRaisesRegex(CalculationDataUnavailable, "MySQL 计算数据仓库不可用"):
            service.detail("000001.SZ")

    def test_single_stock_detail_fails_when_database_baseline_is_missing(self):
        service = self.service
        service._stock = lambda _code: self.fail("must not fall back to another stock source")
        service._calc_repository = SimpleNamespace(available=True, get_calc=lambda _code: {
            "ts_code": "000001.SZ", "symbol": "000001", "name": "测试股票",
            "market": "SZSE", "board": "主板", "stock_return_vector": [],
        })
        service._market_context = lambda: self.fail("must not fall back to remote daily data")

        with self.assertRaisesRegex(CalculationDataUnavailable, "缺少该股票的历史收益向量"):
            service.detail("000001.SZ")

    def test_today_dataset_recomputes_candidate_fields_from_target_day_baseline(self):
        service = self.service
        archived_forecast = {
            "scope": "next_day", "target_trade_date": "20261016", "base_trade_date": "20261015",
            "items": [{"ts_code": "002242.SZ", "ruleKey": "severe_10d", "deviationLabel": "9日 +94.43%"}],
            "status": "ready", "data_stage": "formal", "data_quality": "confirmed",
            "dataset_quote_complete": True, "dataset_quote_as_of": "20261015",
            "dataset_quote_source": "tushare", "dataset_quote_updated_at": "2026-10-15 15:00:00",
            "exclusion_stats": {},
        }
        service._calc_repository = SimpleNamespace(available=False)
        service._latest_data_stage_date = lambda: ("20261016", "formal")
        service.client.trade_cal = lambda *_args: SimpleNamespace(to_dict=lambda *_args, **_kwargs: [
            {"cal_date": "20261016", "is_open": 1}, {"cal_date": "20261019", "is_open": 1},
        ])
        service._prediction_record = lambda scope, target_trade_date="": (
            archived_forecast if scope == "next_day" and target_trade_date == "20261016" else None
        )
        computed_scopes = []

        def compute(scope, **_kwargs):
            computed_scopes.append(scope)
            if scope == "today":
                self.assertEqual(_kwargs["candidate_keys"], {("002242.SZ", "severe_10d")})
                return [{
                    "ts_code": "002242.SZ", "ruleKey": "severe_10d",
                    "deviationLabel": "8日 +96.04%", "deviation": "8日 +96.04%",
                    "change": "+2.77%",
                    "quoteUpdatedAt": "2026-10-16 15:00", "tradeDate": "20261016",
                    "triggerValue": "+2.22%", "scope": "当日",
                }]
            return []

        service._compute_prediction_from_repository = compute
        service._generate_prediction_datasets("formal")

        today_items = service._prediction_cache["today"][1]
        self.assertEqual(computed_scopes, ["today", "next_day"])
        self.assertEqual(today_items[0]["deviationLabel"], "8日 +96.04%")
        self.assertEqual(today_items[0]["change"], "+2.77%")
        self.assertEqual(today_items[0]["quoteUpdatedAt"], "2026-10-16 15:00")
        self.assertEqual(today_items[0]["triggerValue"], "+2.22%")
        self.assertEqual(today_items[0]["targetTradeDate"], "20261016")
        self.assertEqual(today_items[0]["scope"], "当日")
        self.assertEqual(service._prediction_trade_date["today"], "20261016")

    def test_today_dataset_refuses_partial_candidate_recalculation(self):
        service = self.service
        archived_forecast = {
            "scope": "next_day", "target_trade_date": "20261016", "base_trade_date": "20261015",
            "items": [
                {"ts_code": "002242.SZ", "ruleKey": "severe_10d"},
                {"ts_code": "600825.SH", "ruleKey": "severe_10d"},
            ],
            "status": "ready", "dataset_quote_complete": True,
        }
        service._calc_repository = SimpleNamespace(available=False)
        service._latest_data_stage_date = lambda: ("20261016", "formal")
        service.client.trade_cal = lambda *_args: SimpleNamespace(to_dict=lambda *_args, **_kwargs: [
            {"cal_date": "20261016", "is_open": 1}, {"cal_date": "20261019", "is_open": 1},
        ])
        service._prediction_record = lambda scope, target_trade_date="": (
            archived_forecast if scope == "next_day" and target_trade_date == "20261016" else None
        )
        service._compute_prediction_from_repository = lambda scope, **_kwargs: (
            [{"ts_code": "002242.SZ", "ruleKey": "severe_10d", "tradeDate": "20261016"}]
            if scope == "today" else []
        )

        service._generate_prediction_datasets("formal")

        self.assertNotIn("today", service._prediction_cache)
        self.assertIn("1 张前序候选卡片未能按目标日重算", service._prediction_last_error["today"])

    def test_today_dataset_ignores_unrelated_suspended_stock_missing_target_day_quote(self):
        service = self.service
        source = {
            "scope": "next_day", "target_trade_date": "20261016", "base_trade_date": "20261015",
            "items": [{"ts_code": "002242.SZ", "ruleKey": "severe_10d"}],
            "status": "ready", "dataset_quote_complete": True, "exclusion_stats": {},
        }
        candidate_stock = {"ts_code": "002242.SZ", "symbol": "002242", "market": "SZSE", "board": "主板"}
        suspended_stock = {"ts_code": "000016.SZ", "symbol": "000016", "market": "SZSE", "board": "主板"}
        calculations = {
            "002242.SZ": {"as_of_trade_date": "20261016", "quote_updated_at": "2026-10-16 15:00"},
            "000016.SZ": {"as_of_trade_date": "20260903", "quote_updated_at": "2026-09-03 15:00"},
        }
        indexes = {"399107.SZ": {"as_of_trade_date": "20261016", "quote_updated_at": "2026-10-16 15:00"}}
        saved = []
        service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: calculations,
            all_indexes=lambda: indexes,
            save_prediction=lambda *args, **kwargs: saved.append((args, kwargs)),
        )
        service._latest_data_stage_date = lambda: ("20261016", "formal")
        service.client.trade_cal = lambda *_args: SimpleNamespace(to_dict=lambda *_args, **_kwargs: [
            {"cal_date": "20261016", "is_open": 1}, {"cal_date": "20261019", "is_open": 1},
        ])
        service._prediction_record = lambda scope, target_trade_date="": (
            source if scope == "next_day" and target_trade_date == "20261016" else None
        )
        service._stock_rows = lambda: [candidate_stock, suspended_stock]

        def compute(scope, **_kwargs):
            if scope == "today":
                return [{
                    "ts_code": "002242.SZ", "ruleKey": "severe_10d", "tradeDate": "20261016",
                    "quoteUpdatedAt": "2026-10-16 15:00",
                }]
            return []

        service._compute_prediction_from_repository = compute
        service._generate_prediction_datasets("formal")

        self.assertEqual(service._prediction_cache["today"][1][0]["ts_code"], "002242.SZ")
        self.assertTrue(saved)

    def test_today_dataset_refuses_same_day_baseline_when_prior_forecast_missing(self):
        service = self.service
        service._calc_repository = SimpleNamespace(available=False)
        service._latest_data_stage_date = lambda: ("20261016", "formal")
        service.client.trade_cal = lambda *_args: SimpleNamespace(to_dict=lambda *_args, **_kwargs: [
            {"cal_date": "20261016", "is_open": 1}, {"cal_date": "20261019", "is_open": 1},
        ])
        service._prediction_record = lambda *_args, **_kwargs: None
        computed_scopes = []
        service._compute_prediction_from_repository = lambda scope, **_kwargs: computed_scopes.append(scope) or []

        service._generate_prediction_datasets("formal")

        self.assertEqual(computed_scopes, ["next_day"])
        self.assertNotIn("today", service._prediction_cache)
        self.assertIn("缺少 20261016 前一交易日生成的完整次日候选集", service._prediction_last_error["today"])

    def test_single_stock_detail_accepts_partial_common_history(self):
        service = self.service
        service._calc_repository = SimpleNamespace(get_index=lambda _code: {
            "index_code": "399102.SZ", "latest_close": 100.0, "as_of_trade_date": "20261015",
            "index_return_vector": [{"date": "20261015", "return": 0.0}],
        })
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        service._future_trade_dates = lambda *_args: ["2026-10-16"]
        stock = {"ts_code": "301686.SZ", "symbol": "301686", "name": "中塑股份", "market": "SZSE", "board": "创业板", "isST": False}
        calc = {
            "latest_close": 20.0, "as_of_trade_date": "20261015", "data_stage": "formal",
            "stock_return_vector": [{"date": "20261015", "return": 1.0}],
        }

        result = service._detail_from_repository(stock, calc)

        self.assertEqual(result["dataQuality"]["commonDays"], 1)
        self.assertFalse(result["dataQuality"]["isComplete"])
        self.assertEqual(result["warnings"][0]["value"], "+1.00%")
        self.assertEqual(result["nextDayForecast"]["baseTradeDate"], "20261015")
        self.assertEqual(result["nextDayForecast"]["targetTradeDate"], "2026-10-16")
        self.assertTrue(result["nextDayForecast"]["available"])

    def test_search_normalises_input_and_returns_total_matches(self):
        service = self.service
        service._stock_rows = lambda: [
            {"ts_code": "601881.SH", "symbol": "601881", "name": "中国银河", "nameInitials": "ZGYH"},
            {"ts_code": "601988.SH", "symbol": "601988", "name": "中国银行", "nameInitials": "ZGYH"},
            {"ts_code": "601999.SH", "symbol": "601999", "name": "出口银行", "nameInitials": "CKYH"},
            {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "nameInitials": "PAYH"},
        ]

        result = service.search("  zgyh ")

        self.assertEqual(result["total"], 2)
        self.assertFalse(result["hasMore"])
        self.assertEqual([item["name"] for item in result["items"]], ["中国银河", "中国银行"])

    def test_history_coverage_classifies_missing_recent_listing_and_unresolved_gaps(self):
        service = self.service
        dates = [f"202608{day:02d}" for day in range(1, 31)]
        vector = [{"date": value, "return": 0.0} for value in dates]
        stocks = [
            {"ts_code": "000001.SZ", "name": "完整", "market": "SSE", "board": "主板", "list_date": "20000101", "isST": False, "priceLimitEnabled": True},
            {"ts_code": "000002.SZ", "name": "新股", "market": "SSE", "board": "主板", "list_date": dates[-5], "isST": False, "priceLimitEnabled": True},
            {"ts_code": "000003.SZ", "name": "缺日", "market": "SSE", "board": "主板", "list_date": "20000101", "isST": False, "priceLimitEnabled": True},
            {"ts_code": "000004.SZ", "name": "缺向量", "market": "SSE", "board": "主板", "list_date": "20000101", "isST": False, "priceLimitEnabled": True},
            {"ts_code": "000005.SZ", "name": "源缺记录", "market": "SSE", "board": "主板", "list_date": "20000101", "isST": False, "priceLimitEnabled": True},
        ]
        calculations = {
            "000001.SZ": {"as_of_trade_date": dates[-1], "stock_return_vector": vector},
            "000002.SZ": {"as_of_trade_date": dates[-1], "stock_return_vector": vector[-5:]},
            "000003.SZ": {"as_of_trade_date": dates[-1], "stock_return_vector": [item for index, item in enumerate(vector) if index != 12]},
            "000005.SZ": {"as_of_trade_date": dates[-1], "stock_return_vector": [item for index, item in enumerate(vector) if index != 14]},
        }
        service._calc_repository = SimpleNamespace(
            available=True,
            history_coverage_snapshot=lambda: (
                stocks,
                calculations,
                {"000001.SH": {"as_of_trade_date": dates[-1], "index_return_vector": vector}},
            ),
        )
        service.client.suspend_d = lambda *_args: SimpleNamespace(to_dict=lambda *_args, **_kwargs: [
            {"ts_code": "000003.SZ", "trade_date": dates[12], "suspend_type": "S"},
        ])

        result = service.history_coverage(limit=2)

        self.assertEqual(result["asOfTradeDate"], dates[-1])
        self.assertEqual(result["requiredReturnCount"], 30)
        self.assertEqual(result["activeStockCount"], 5)
        self.assertEqual(result["completeStockCount"], 1)
        self.assertEqual(result["incompleteStockCount"], 4)
        self.assertEqual(result["suspensionStatusSource"], "tushare.suspend_d")
        self.assertEqual(result["categoryCounts"], {
            "recent_listing_short_history": 1,
            "suspended_history_short": 1,
            "missing_stock_vector": 1,
            "upstream_market_data_missing": 1,
        })
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["pagination"]["total"], 4)

    def test_calculation_bases_returns_persisted_vectors_and_index_without_intraday_data(self):
        service = self.service
        service._calc_repository = SimpleNamespace(
            available=True,
            calculation_bases=lambda **_kwargs: {
                "activeStockCount": 1,
                "stocks": [{
                    "ts_code": "000001.SZ", "symbol": "000001", "name": "测试",
                    "market": "SZSE", "board": "主板", "isST": 0,
                    "list_status": "L", "list_date": "20200101", "delist_date": "",
                    "priceLimitEnabled": True, "as_of_trade_date": "20261015",
                    "latest_close": 10.0, "stock_return_vector": [{"date": "20261015", "return": 1.2}],
                    "deviation_3": 1.2, "deviation_10": 2.3, "deviation_30": 4.5,
                    "same_direction_up": 3, "same_direction_down": 0,
                    "data_stage": "formal", "quote_updated_at": None, "updated_at": None,
                }],
                "indexes": [{
                    "index_code": "399107.SZ", "index_name": "深证成指",
                    "as_of_trade_date": "20261015", "latest_close": 1000,
                    "index_return_vector": [{"date": "20261015", "return": 0.5}],
                    "data_stage": "formal", "quote_updated_at": None, "updated_at": None,
                }],
            },
        )
        result = service.calculation_bases(ts_codes=["000001.SZ"], limit=1)
        self.assertEqual(result["returnedStockCount"], 1)
        self.assertEqual(result["items"][0]["indexCode"], "399107.SZ")
        self.assertEqual(result["indexes"][0]["returnVector"][0]["return"], 0.5)
        self.assertFalse(result["containsRawDaily"])
        self.assertFalse(result["containsIntradaySnapshot"])

    def test_persisted_dataset_and_jobs_are_read_only_wrappers(self):
        service = self.service
        service._calc_repository = SimpleNamespace(
            available=True,
            prediction_dataset_statuses=lambda: [{"scope": "today", "target_trade_date": "20261015", "item_count": 3}],
            jobs_snapshot=lambda: {"market": [{"job_name": "daily"}], "prediction": [{"job_name": "today"}]},
        )
        self.assertEqual(service.persisted_dataset_statuses()["count"], 1)
        jobs = service.persisted_jobs()
        self.assertEqual(jobs["market"][0]["job_name"], "daily")
        self.assertEqual(jobs["prediction"][0]["job_name"], "today")

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

    def test_today_threshold_uses_n_prior_stock_returns_and_target_day_index(self):
        stock = [{"date": f"2026-10-{index:02d}", "return": 5.0} for index in range(1, 11)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        index[-1]["return"] = 1.0
        stock_total = MarketService._compound([5.0] * 9)
        expected = ((1.0 + (1.0 + 100.0) / 100.0) / (1.0 + stock_total / 100.0) - 1.0) * 100.0
        actual = MarketService._required_up_percent(stock, index, 8, 100.0, "today", 10)
        self.assertAlmostEqual(actual, expected, places=5)

    def test_today_three_day_threshold_caps_known_stock_returns_at_two(self):
        stock = [{"date": f"2026-10-{day:02d}", "return": value} for day, value in enumerate([0.0] * 7 + [5.0, 5.0, 0.0], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        index[-4]["return"] = 1.0
        index[-1]["return"] = 1.0
        stock_total = MarketService._compound([5.0, 5.0])
        index_total = MarketService._compound([0.0, 0.0, 1.0])
        expected = ((1.0 + (index_total + 30.0) / 100.0) / (1.0 + stock_total / 100.0) - 1.0) * 100.0

        actual = MarketService._required_up_percent(stock, index, 3, 30.0, "today", 3)

        self.assertAlmostEqual(actual, expected, places=5)

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

    def test_detail_keeps_monitoring_fact_and_market_trigger_visible_together(self):
        service = self.service
        dates = [f"202610{day:02d}" for day in range(1, 16)]
        stock_vector = [{"date": day, "return": 150.0 if day == dates[-1] else 0.0} for day in dates]
        index_vector = [{"date": day, "return": 0.0} for day in dates]
        service._calc_repository = SimpleNamespace(get_index=lambda _code: {
            "index_code": "000001.SH", "latest_close": 10.0, "as_of_trade_date": dates[-1],
            "index_return_vector": index_vector,
        })
        service._realtime = SimpleNamespace(fetch_many=lambda _codes: {})
        service._clock = lambda: datetime(2026, 10, 15)
        service._future_trade_dates = lambda *_args: []
        service.set_monitor_provider(lambda: {"items": [{
            "symbol": "600000", "monitorType": "10日严重异动", "title": "严重异常波动",
            "monitorDates": ["2026-10-15"], "monitorEndDate": "2026-10-20",
        }]})
        stock = {"ts_code": "600000.SH", "symbol": "600000", "name": "测试股票", "market": "SSE", "board": "主板", "isST": False}
        calc = {"latest_close": 10.0, "as_of_trade_date": dates[-1], "data_stage": "formal", "stock_return_vector": stock_vector}

        result = service._detail_from_repository(stock, calc)

        self.assertEqual(result["statusDisplay"], "监管中")
        self.assertEqual(result["statusClass"], "monitoring")
        self.assertEqual(result["statusIcon"], "!")
        self.assertEqual(result["monitoringStatus"], "active")
        self.assertEqual(result["monitoringStatusDisplay"], "监管中")
        self.assertTrue(result["monitoring"])
        self.assertIn("已触发", result["marketStatusDisplay"])
        self.assertIn("10 日严重异动", result["statusReason"])
        self.assertIn("3 日普通异动", result["statusReason"])
        self.assertTrue(all("上涨阈值" not in item["target"] for item in result["warnings"]))

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

    def test_same_direction_counts_adjacent_non_overlapping_three_day_events(self):
        """向量口径与日线口径一致：相邻事件共享收盘点但不共享收益区间。"""
        dates = [f"2026-08-{index:02d}" for index in range(1, 8)]
        stock = [{"date": day, "return": 10.0} for day in dates]
        index = [{"date": day, "return": 0.0} for day in dates]
        metrics = MarketService._vector_metrics(stock, index, BOARD_RULES[Board.MAIN])
        self.assertEqual(metrics["up"], 2)

    def test_single_stock_metrics_keep_downside_extremes_and_same_direction_count(self):
        dates = [f"2026-08-{index:02d}" for index in range(1, 11)]
        stock = [{"date": day, "return": -10.0} for day in dates]
        index = [{"date": day, "return": 0.0} for day in dates]
        metrics = MarketService._vector_metrics(stock, index, BOARD_RULES[Board.MAIN])
        self.assertLessEqual(metrics["down_deviations"][10], -50.0)
        self.assertEqual(metrics["down"], 3)
        status, _ = MarketService._status_for_metrics(
            metrics["deviations"], metrics["up"], BOARD_RULES[Board.MAIN],
            metrics["down_deviations"], metrics["down"],
        )
        self.assertEqual(status, "触及")

    def test_same_direction_does_not_pair_returns_without_common_dates(self):
        """无共同日期时不能按数组位置错配股票和指数收益。"""
        stock = [{"date": f"2026-08-{index:02d}", "return": 10.0} for index in range(1, 5)]
        index = [{"date": f"2026-09-{index:02d}", "return": 0.0} for index in range(1, 5)]
        metrics = MarketService._vector_metrics(stock, index, BOARD_RULES[Board.MAIN])
        self.assertEqual(metrics["up"], 0)
        self.assertIsNone(metrics["deviations"][3])

    def test_ten_day_vector_window_can_use_ten_daily_returns(self):
        dates = [f"2026-08-{index:02d}" for index in range(1, 12)]
        stock = [{"date": day, "return": 8.0} for day in dates]
        index = [{"date": day, "return": 0.0} for day in dates]
        metrics = MarketService._vector_metrics(stock, index, BOARD_RULES[Board.MAIN])
        self.assertGreaterEqual(metrics["deviations"][10], 100.0)
        self.assertEqual(metrics["window_lengths"][10], 10)

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

    def test_single_stock_next_day_candidates_include_upside_and_downside_rules(self):
        dates = [f"2026-09-{day:02d}" for day in range(1, 31)]
        index = [{"date": day, "return": 0.0} for day in dates]
        up_stock = [{"date": day, "return": 0.0} for day in dates]
        down_stock = [{"date": day, "return": 0.0} for day in dates]
        up_stock[-2]["return"] = up_stock[-1]["return"] = 8.0
        down_stock[-2]["return"] = down_stock[-1]["return"] = -8.0
        rule = BOARD_RULES[Board.MAIN]
        up_metrics = MarketService._vector_metrics(up_stock, index, rule)
        down_metrics = MarketService._vector_metrics(down_stock, index, rule)

        upside = MarketService._next_day_rule_candidates(up_stock, index, rule, "2026-10-01", up_metrics, limit_price_ratio=10)
        downside = MarketService._next_day_rule_candidates(down_stock, index, rule, "2026-10-01", down_metrics, limit_price_ratio=10)

        self.assertIn(("ordinary_3d", "up"), {(item["ruleKey"], item["direction"]) for item in upside})
        self.assertIn(("ordinary_3d", "down"), {(item["ruleKey"], item["direction"]) for item in downside})

    def test_single_stock_next_day_candidates_include_same_direction_rule(self):
        stock = [{"date": f"2026-09-{day:02d}", "return": 10.0} for day in range(1, 9)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        rule = BOARD_RULES[Board.CHINEXT]
        metrics = MarketService._vector_metrics(stock, index, rule)

        candidates = MarketService._next_day_rule_candidates(
            stock, index, rule, "2026-09-09", metrics, limit_price_ratio=20,
        )

        self.assertEqual(metrics["up"], 2)
        self.assertIn(("same_direction_10d", "up"), {(item["ruleKey"], item["direction"]) for item in candidates})

    def test_ten_day_reset_does_not_suppress_single_stock_thirty_day_candidate(self):
        dates = [f"2026-09-{day:02d}" for day in range(1, 21)]
        stock = [{"date": day, "return": 0.0} for day in dates]
        index = [{"date": day, "return": 0.0} for day in dates]
        for position in range(9, 20):
            stock[position]["return"] = 10.0
        stock[19]["return"] = 10.0
        rule = BOARD_RULES[Board.CHINEXT]
        metrics = MarketService._vector_metrics(stock, index, rule, "2026-09-11", False)

        candidates = MarketService._next_day_rule_candidates(
            stock, index, rule, "2026-09-21", metrics,
            rule_reset_date="2026-09-11", reset_window=10, limit_price_ratio=20,
        )

        self.assertIn(("severe_30d", "up"), {(item["ruleKey"], item["direction"]) for item in candidates})

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
        self.assertTrue(any(item["deviation"].startswith("10日 ") for item in items))

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

    def test_monitored_stock_adds_only_ordinary_short_window_prediction_card(self):
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
        self.assertNotIn("same_direction_10d", keys)

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

    def test_window_must_end_at_latest_common_day(self):
        # 早期峰值已结束，不能继续作为当前偏离；只比较以最新日为终点的区间。
        values = [0.0, 0.0, 10.0, 10.0, 1.0, 1.0, 1.0, 1.0, -5.0, -50.0]
        stock = [{"date": f"2026-10-{index:02d}", "return": value} for index, value in enumerate(values, 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 10)
        self.assertEqual(window, 2)
        self.assertAlmostEqual(value, -52.5)

    def test_best_trailing_window_matches_nearshore_october_16_reference(self):
        # 10/16 正式股票/指数向量的最佳、且以当日为终点的 30 日窗口为 23 日 +142.33%。
        stock_values = [-11.288566243194207, -6.205673758865249, 5.40933546604625, 0.3172851427783119, -12.307480748074806,
            -2.540379488787836, -0.933226065969428, 15.332142277082994, 0.9012815096465232,
            -2.763433356594558, -8.138366585330846, 19.999999999999996, 0.390625,
            8.949416342412464, 18.75, -0.7218045112781901, 20.00403918004645,
            4.76270615954224, -1.3333333333333308, 1.9619016606968296, 2.259481037924171,
            10.360712054965626, 13.47010965688007, -6.47795997256686, 7.453333333333334,
            -8.81623030152625, -6.674831598285369, 4.403616214639827, 5.572625698324041,
            -7.785421352030697]
        index_values = [-1.8500547629862083, 1.3379062879610215, -2.1878816113135025, -1.8171511875105129, -0.3975960138890122,
            -2.098146111894672, 2.419858845212697, -1.5187023705216407, -0.6874061358065564,
            -0.6860883911408822, -1.0084129791510188, -1.6176127885134115, 1.5502535847651044,
            4.138481573697117, -0.6124729734010681, 2.8851695173508496, 0.29368927485928786,
            0.4559439043958502, -0.2515011935436129, -2.347117525427578, -4.062688054297759,
            0.8584941044244676, -2.5063236839167446, -4.815785980405163, 0.06472461242996719,
            -2.096040772226626, 0.03195437708378712, -3.287593869718375, -1.5023498872710483,
            -0.06399317406142746]
        dates = [f"2026-10-{index:02d}" for index in range(1, 31)]
        stock = [{"date": day, "return": value} for day, value in zip(dates, stock_values)]
        index = [{"date": day, "return": value} for day, value in zip(dates, index_values)]
        value, window = MarketService._best_vector_deviation(stock, index, 30)
        self.assertEqual(window, 23)
        self.assertAlmostEqual(value, 142.3258, places=3)

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

    def test_three_day_window_uses_maximum_interval_ending_at_latest_date(self):
        stock = [{"date": f"2026-09-{index:02d}", "return": value} for index, value in enumerate([80.0, 0.0, 0.0, 0.0, 0.0], 1)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        value, window = MarketService._best_vector_deviation(stock, index, 3)
        self.assertEqual(window, 1)
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

    def test_severe_announcement_reset_keeps_partial_windows_calculable(self):
        """公告重置后历史不足完整 10/30 日时，仍按现有有效区间计算。"""
        service = self.service
        service._clock = lambda: datetime(2026, 10, 10)
        service.set_monitor_provider(lambda: {"items": [{
            "symbol": "301190", "riskTone": "severe-30d",
            "monitorDates": ["2026-10-09", "2026-10-10", "2026-10-13"],
            "monitorEndDate": "2026-10-22",
        }]})
        stock = {"ts_code": "301190.SZ", "symbol": "301190"}
        stock_vector = [
            {"date": "2026-10-08", "return": 80.0},
            {"date": "2026-10-09", "return": 10.0},
            {"date": "2026-10-10", "return": 20.0},
        ]
        index_vector = [{"date": item["date"], "return": 0.0} for item in stock_vector]
        trimmed_stock, trimmed_index, meta, monitoring = service._rule_vectors_for_stock(
            stock, stock_vector, index_vector
        )
        self.assertEqual(meta["ruleResetDate"], "20261010")
        self.assertEqual([item["date"] for item in trimmed_stock], ["2026-10-08", "2026-10-09", "2026-10-10"])
        self.assertEqual(monitoring["riskTone"], "severe-30d")
        metrics = service._vector_metrics(trimmed_stock, trimmed_index, BOARD_RULES[Board.CHINEXT], meta["ruleResetDate"], True)
        self.assertEqual(metrics["window_lengths"][30], 1)
        self.assertAlmostEqual(metrics["deviations"][30], 20.0)

    def test_official_severe_notice_reset_date_survives_broker_primary_label(self):
        """10/08 官方公告确认触发周期结束后，从 10/09 的收益区间重新累计。"""
        service = self.service
        service._clock = lambda: datetime(2026, 10, 16)
        service.set_monitor_provider(lambda: {"items": [{
            "symbol": "301190", "riskTone": "severe-10d",
            "sourceType": "broker-risk-alert", "sourceRole": "broker_primary",
            "ruleResetConfirmed": True, "ruleResetDate": "20261009",
            "monitorDates": ["2026-10-09", "2026-10-12", "2026-10-13"],
            "monitorEndDate": "2026-10-22",
        }]})
        stock = {"ts_code": "301190.SZ", "symbol": "301190"}
        dates = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
        stock_vector = [{"date": day, "return": 50.0 if day == dates[0] else 2.0} for day in dates]
        index_vector = [{"date": day, "return": 0.0} for day in dates]

        trimmed_stock, trimmed_index, meta, _ = service._rule_vectors_for_stock(stock, stock_vector, index_vector)

        self.assertEqual(meta["ruleResetDate"], "20261009")
        self.assertEqual([item["date"] for item in trimmed_stock], dates)
        self.assertEqual([item["date"] for item in trimmed_index], dates)
        metrics = service._vector_metrics(trimmed_stock, trimmed_index, BOARD_RULES[Board.CHINEXT], meta["ruleResetDate"], False)
        self.assertAlmostEqual(metrics["deviations"][3], 6.1208, places=3)
        self.assertEqual(metrics["window_lengths"][30], 4)

    def test_ten_day_announcement_reset_does_not_cut_rolling_thirty_day_window(self):
        daily_return = ((1 + 142.32581068 / 100) ** (1 / 23) - 1) * 100
        stock = [{"date": f"2026-09-{day:02d}", "return": daily_return} for day in range(1, 24)]
        index = [{"date": item["date"], "return": 0.0} for item in stock]
        metrics = MarketService._vector_metrics(
            stock, index, BOARD_RULES[Board.CHINEXT], "20260918", False
        )
        self.assertEqual(metrics["window_lengths"][30], 23)
        self.assertAlmostEqual(metrics["deviations"][30], 142.32581068, places=6)
        self.assertEqual(metrics["window_lengths"][10], 6)

    def test_xinhua_resume_counts_one_valid_return_after_three_day_suspension(self):
        """新华传媒 10/13-10/15 停牌、10/16 复牌，不能把停牌日算成四个行情日。"""
        service = self.service
        service._clock = lambda: datetime(2026, 10, 16)
        service.set_monitor_provider(lambda: {"items": [{
            "symbol": "600825", "riskTone": "severe-10d", "sourceType": "issuer-disclosure",
            "monitorType": "10日严重异动",
            "monitorDates": ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"],
            "monitorEndDate": "2026-10-23",
        }]})
        stock = {"ts_code": "600825.SH", "symbol": "600825"}
        stock_vector = [
            {"date": "2026-10-12", "return": 10.0},
            {"date": "2026-10-16", "return": 5.0},
        ]
        index_vector = [{"date": day, "return": 0.0} for day in ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")]
        trimmed_stock, trimmed_index, meta, monitoring = service._rule_vectors_for_stock(stock, stock_vector, index_vector)
        self.assertEqual([item["date"] for item in trimmed_stock], ["2026-10-16"])
        self.assertEqual([item["date"] for item in trimmed_index], ["2026-10-16"])
        self.assertEqual(meta["resetDate"], "20261016")
        self.assertEqual(meta["ruleResetDate"], "20261013")
        self.assertEqual(monitoring["monitorType"], "10日严重异动")

    def test_broker_risk_alert_does_not_reset_market_window(self):
        """18.cn 风险提示不是交易所正文公告，不得截断行情累计。"""
        service = self.service
        service._clock = lambda: datetime(2026, 10, 10)
        service.set_monitor_provider(lambda: {"items": [{
            "symbol": "688137", "riskTone": "severe-10d",
            "sourceType": "broker-risk-alert",
            "monitorDates": ["2026-10-08", "2026-10-09", "2026-10-12"],
            "monitorEndDate": "2026-10-21",
        }]})
        stock = {"ts_code": "688137.SH", "symbol": "688137"}
        values = [{"date": "2026-10-08", "return": 1.0}, {"date": "2026-10-09", "return": 2.0}]
        _, _, meta, _ = service._rule_vectors_for_stock(stock, values, [{"date": x["date"], "return": 0.0} for x in values])
        self.assertEqual(meta["ruleResetDate"], "")

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
