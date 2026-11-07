import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from server.services.market_service import MarketService, PREDICTION_RULE_VERSION


class ProductionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parents[1] / "fixtures/production_regression_20261101.json").read_text())
        rows = self.fixture["market"]["items"]
        indexes = self.fixture["market"]["indexes"]
        self.service = MarketService()
        self.service.set_clock(lambda: datetime(2026, 11, 1, 22, 0))
        self.stocks = {x["ts_code"]: dict(x, isST=x["isST"], list_date=x["listDate"], list_status=x["listStatus"]) for x in rows}
        self.calcs = {x["ts_code"]: dict(self.stocks[x["ts_code"]], as_of_trade_date=x["asOfTradeDate"], latest_close=x["latestClose"], stock_return_vector=x["returnVector"]) for x in rows}
        self.indexes = {x["indexCode"]: dict(index_code=x["indexCode"], as_of_trade_date=x["asOfTradeDate"], latest_close=x["latestClose"], index_return_vector=x["returnVector"]) for x in indexes}
        self.service._stock_rows = lambda: list(self.stocks.values())
        self.service._calc_repository = SimpleNamespace(available=True, all_calculations=lambda: self.calcs, all_indexes=lambda: self.indexes,
            get_calc=lambda code: self.calcs[code], get_index=lambda code: self.indexes[code])
        self.service.set_monitor_provider(lambda: self.fixture["monitor"])
        self.service._fetch_realtime_many = lambda codes: {}
        xinhua_stock_dates = {item["date"].replace("-", "") for item in self.calcs["600825.SH"]["stock_return_vector"]}
        xinhua_index_dates = {
            item["date"].replace("-", "") for item in self.indexes["000002.SH"]["index_return_vector"]
            if item["date"].replace("-", "") < self.calcs["600825.SH"]["as_of_trade_date"]
        }
        xinhua_suspended_dates = sorted(xinhua_index_dates - xinhua_stock_dates)
        self.service.client.suspend_d = lambda *_args: SimpleNamespace(to_dict=lambda *_a, **_k: [
            {"ts_code": "600825.SH", "trade_date": day, "suspend_type": "S"}
            for day in xinhua_suspended_dates
        ])

    def tearDown(self):
        self.service.close()

    def test_announced_future_reset_does_not_remove_today_triggered_card(self):
        cards = self.service._compute_prediction_from_repository("today", target_trade_date="20261030")
        by_key = {(x["ts_code"], x["ruleKey"]): x for x in cards}
        for code in ("603278.SH", "002724.SZ"):
            self.assertTrue(by_key[(code, "severe_10d")]["triggered"], code)

    def test_weekend_monitor_eligibility_uses_prediction_target(self):
        codes = self.service._monitored_rule_codes("20261030")
        self.assertIn("301313", codes["__severe_monitor__"])

    def test_user_reported_today_candidates_are_present(self):
        cards = self.service._compute_prediction_from_repository("today", target_trade_date="20261030")
        keys = {(x["ts_code"], x["ruleKey"]) for x in cards}
        for code in ("688836.SH", "301502.SZ", "688585.SH", "688286.SH"):
            self.assertIn((code, "severe_10d"), keys)
        self.assertIn(("301313.SZ", "ordinary_3d"), keys)

    def test_negative_valid_deviation_is_not_missing(self):
        stock = self.calcs["600825.SH"]
        index = self.indexes["000002.SH"]
        from server.core.abnormal_rules import BOARD_RULES, Board
        metrics = self.service._vector_metrics(stock["stock_return_vector"], index["index_return_vector"], BOARD_RULES[Board.MAIN])
        self.assertIsNotNone(metrics["up_deviations"][3])
        self.assertIsNotNone(metrics["up_deviations"][10])

    def test_prediction_and_detail_share_same_current_deviation_and_trigger(self):
        cards = self.service._compute_prediction_from_repository("today", target_trade_date="20261030")
        warnings = {"ordinary_3d": "3 日偏离", "severe_10d": "10 日偏离", "severe_30d": "30 日偏离"}
        self.service._future_trade_dates = lambda *_: ["20261102"]
        for card in cards:
            if card["ruleKey"] not in warnings:
                continue
            code = card["ts_code"]
            with self.subTest(code=code, rule=card["ruleKey"]):
                detail = self.service._detail_from_repository(self.stocks[code], self.calcs[code], use_realtime=False)
                warning = next(x for x in detail["warnings"] if x["title"] == warnings[card["ruleKey"]])
                self.assertEqual(card["deviation"].split()[-1], warning["upValue"])
                self.assertEqual(card["triggered"], warning["upTriggered"])

    def test_xinhua_detail_is_not_unknown_for_valid_negative_short_history(self):
        self.service._future_trade_dates = lambda *_: ["20261102"]
        detail = self.service._detail_from_repository(self.stocks["600825.SH"], self.calcs["600825.SH"], use_realtime=False)
        self.assertTrue(detail["dataQuality"]["isComplete"])
        for warning in (detail["warnings"][0], detail["warnings"][2], detail["warnings"][3]):
            self.assertNotEqual(warning["value"], "--")
        self.assertNotEqual(detail["stockState"]["status"], "unknown")

    def test_rule_upgrade_repairs_prior_candidate_selection_from_prior_close(self):
        self.service._calc_repository.save_prediction = Mock()
        repaired = self.service._repair_prior_candidates({"rule_version": "v68", "base_trade_date": "20261029",
            "target_trade_date": "20261030", "items": []}, "20261030")
        keys = {(x["ts_code"], x["ruleKey"]) for x in repaired["items"]}
        # The index fell on 20261030: these stocks were legitimately outside
        # the prior-close pool. The replay must not look ahead to that fall.
        self.assertNotIn(("688836.SH", "severe_10d"), keys)
        self.assertEqual(repaired["base_trade_date"], "20261029")
        self.assertEqual(repaired["rule_version"], PREDICTION_RULE_VERSION)

    def test_next_session_reset_cannot_reuse_ocean_king_or_daye_pre_reset_threshold(self):
        cards = self.service._compute_prediction_from_repository("next_day", target_trade_date="20261102")
        keys = {(x["ts_code"], x["ruleKey"]) for x in cards}
        self.assertNotIn(("002724.SZ", "ordinary_3d"), keys)
        self.assertNotIn(("603278.SH", "ordinary_3d"), keys)

    def test_incomplete_current_version_candidates_are_rebuilt_from_prior_close(self):
        self.service._calc_repository.save_prediction = Mock()
        source = {"rule_version": PREDICTION_RULE_VERSION, "base_trade_date": "20261030",
            "target_trade_date": "20261030", "items": [], "status": "incomplete",
            "dataset_quote_complete": False}
        repaired = self.service._repair_prior_candidates(source, "20261030")
        self.assertEqual(repaired["base_trade_date"], "20261029")
        self.assertEqual(repaired["target_trade_date"], "20261030")
        self.assertEqual(repaired["rule_version"], PREDICTION_RULE_VERSION)
        self.assertEqual(repaired["status"], "ready")
        self.assertTrue(repaired["dataset_quote_complete"])
        self.service._calc_repository.save_prediction.assert_called_once()

    def test_missing_prior_candidates_are_rebuilt_from_persisted_history(self):
        self.service._calc_repository.save_prediction = Mock()
        repaired = self.service._repair_prior_candidates(None, "20261030")
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["base_trade_date"], "20261029")
        self.assertEqual(repaired["target_trade_date"], "20261030")
        self.assertEqual(repaired["rule_version"], PREDICTION_RULE_VERSION)
        self.assertTrue(repaired["dataset_quote_complete"])
        self.service._calc_repository.save_prediction.assert_called_once()

    def test_today_recompute_failure_does_not_overwrite_dataset_with_empty_items(self):
        repository = self.service._calc_repository
        repository.save_prediction = Mock()
        repository.fail_prediction = Mock()
        self.service._latest_data_stage_date = lambda: ("20261030", "formal")
        self.service.client.trade_cal = lambda *_: SimpleNamespace(to_dict=lambda *_: [
            {"cal_date": "20261030", "is_open": 1}, {"cal_date": "20261102", "is_open": 1}])
        self.service._prediction_record = lambda *_args, **_kwargs: None
        repository.all_calculations = lambda: {}
        repository.all_indexes = lambda: {}
        self.service._compute_prediction_from_repository = lambda *_args, **_kwargs: None
        self.service._generate_prediction_datasets("formal")
        today_writes = [call for call in repository.save_prediction.call_args_list
            if call.args and call.args[0] == "today"]
        self.assertEqual(today_writes, [])
        repository.fail_prediction.assert_any_call("today", unittest.mock.ANY)

    def test_formal_close_supplements_new_candidates_without_intraday_scanning(self):
        self.service._calc_repository.save_prediction = Mock()
        self.service._calc_repository.fail_prediction = Mock()
        self.service._latest_data_stage_date = lambda: ("20261030", "formal")
        for row in [*self.calcs.values(), *self.indexes.values()]:
            row["quote_updated_at"] = "2026-10-30 15:00:00"
        self.service.client.trade_cal = lambda *_: SimpleNamespace(to_dict=lambda *_: [
            {"cal_date": "20261030", "is_open": 1}, {"cal_date": "20261102", "is_open": 1}])
        self.service._prediction_record = lambda *_: {"rule_version": "v68", "base_trade_date": "20261029",
            "target_trade_date": "20261030", "items": [], "status": "ready", "dataset_quote_complete": True}
        self.service._generate_prediction_datasets("formal")
        keys = {(x["ts_code"], x["ruleKey"]) for x in self.service._prediction_cache["today"][1]}
        for code in ("688836.SH", "301502.SZ", "688585.SH", "688286.SH", "603278.SH", "002724.SZ"):
            self.assertIn((code, "severe_10d"), keys)
        self.assertIn(("301313.SZ", "ordinary_3d"), keys)

    def test_ocean_king_three_notices_count_before_effective_severe_reset(self):
        from server.core.abnormal_rules import BOARD_RULES, Board
        stock = self.calcs["002724.SZ"]["stock_return_vector"]
        index = self.indexes["399107.SZ"]["index_return_vector"]
        events = [{"sourceId": f"notice-{i}", "endDate": end, "direction": "up", "windowDays": 2}
                  for i, end in enumerate(("20261019", "20261021", "20261023"))]
        metrics = self.service._vector_metrics(stock, index, BOARD_RULES[Board.MAIN],
            ordinary_events=events, severe_reset_dates=["20261102"])
        self.assertEqual(metrics["up"], 3)
        self.assertEqual(metrics["unresolved_same_direction_events"], [])
        reset = self.service._vector_metrics([*stock, {"date": "20261102", "return": 0.0}],
            [*index, {"date": "20261102", "return": 0.0}], BOARD_RULES[Board.MAIN],
            ordinary_events=events, severe_reset_dates=["20261102"])
        self.assertEqual(reset["up"], 0)
        self.assertEqual(reset["deviations"][10], 0.0)
