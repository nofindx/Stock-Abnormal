import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from server.services.market_service import MarketService


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
        self.assertEqual(repaired["rule_version"], "v69")

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
