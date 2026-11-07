"""T1: numerical checks against the production engine, not only replay labels.

Pause/resume policy helpers are deliberately not used to certify the production
path: its migration gap is tracked in the release report.
"""
import json
import unittest
from pathlib import Path

from server.rules.catalog import BOARD_RULES, Board
from server.services.market_service import MarketService, _index_code_for_stock


def vector(dates, values):
    return [{"date": day, "return": value} for day, value in zip(dates, values)]


class ReleaseRecalculationTests(unittest.TestCase):
    dates = ["20261019", "20261020", "20261021", "20261022", "20261023"]

    def test_product_fixture_thresholds_match_production_catalog(self):
        fixture = json.loads((Path(__file__).parents[1] / "fixtures/vnext/product_policy_cases.json").read_text())
        for case in fixture["cases"]:
            if not case["id"].startswith("F-RULE-BOARD-"):
                continue
            with self.subTest(fixture=case["id"]):
                rule = BOARD_RULES[Board[case["input"]["board"]]]
                self.assertEqual([rule.ordinary_deviation, rule.severe_10d_threshold.up,
                                  rule.severe_30d_threshold.up, rule.severe_same_direction_count],
                                 case["input"]["thresholds"])

    def test_daily_return_prefers_vendor_adjusted_change_and_falls_back_to_pre_close(self):
        adjusted = MarketService._daily_return_entry(
            {"close": 5.1, "pre_close": 5.0, "pct_chg": 2.0}, "20261022")
        fallback = MarketService._daily_return_entry(
            {"close": 5.1, "pre_close": 5.0, "pct_chg": None}, "20261022")
        invalid_fallback = MarketService._daily_return_entry(
            {"close": 5.1, "pre_close": 0, "pct_chg": "nan"}, "20261022")
        self.assertEqual(adjusted, {"date": "20261022", "return": 2.0})
        self.assertAlmostEqual(fallback["return"], 2.0)
        self.assertIsNone(invalid_fallback)

    def test_daily_return_is_used_for_incremental_and_history_vectors(self):
        service = MarketService()
        try:
            calculations = {"600000.SH": {"ts_code": "600000.SH", "latest_close": 10.0,
                "as_of_trade_date": "20261021", "stock_return_vector": []}}
            frame = type("Frame", (), {"empty": False, "to_dict": lambda self, *_: [{
                "ts_code": "600000.SH", "trade_date": "20261022", "close": 5.1,
                "pre_close": 5.0, "pct_chg": 2.0}]})()
            service._append_daily_rows(calculations, {"20261022": frame}, ["20261022"])
            self.assertEqual(calculations["600000.SH"]["stock_return_vector"][-1]["return"], 2.0)
        finally:
            service.close()

    def test_all_five_product_index_mappings_match_production(self):
        cases = [("主板", "SSE", "000002.SH"), ("主板", "SZSE", "399107.SZ"),
                 ("创业板", "SZSE", "399102.SZ"), ("科创板", "SSE", "000688.SH"),
                 ("北交所", "BSE", "899050.BJ")]
        for board, market, expected in cases:
            with self.subTest(board=board, market=market):
                self.assertEqual(_index_code_for_stock({"board": board, "market": market}), expected)

    def test_compound_deviation_uses_paired_sessions_not_sum_or_relative_ratio(self):
        dates = ["20260930", "20261008", "20261009"]
        stock = vector(dates, [10, 5, 2])
        index = vector(dates, [1, 2, -1])
        # Independently calculated products: stock=17.81%, index=1.9898%.
        actual = MarketService._vector_deviation(stock, index, 3)
        self.assertAlmostEqual(actual, 15.8202, places=8)
        # Missing a matching index session must not be filled with zero.
        self.assertIsNone(MarketService._vector_deviation(stock, index[:-1], 3))

    def test_confirmed_notice_counts_even_when_market_does_not_reproduce_threshold(self):
        stock = vector(self.dates, [1, 1, 1, 1, 1])
        index = vector(self.dates, [0] * 5)
        event = {"sourceId": "confirmed-ordinary", "endDate": "20261021", "direction": "up"}
        metrics = MarketService._vector_metrics(stock, index, BOARD_RULES[Board.MAIN],
            ordinary_events=[event, dict(event)], ordinary_reset_dates=["20261023"])
        self.assertEqual(metrics["up"], 1)  # same notice read twice is one event
        self.assertEqual(metrics["deviations"][3], 0)
        self.assertAlmostEqual(metrics["deviations"][10], 5.10100501, places=8)
        self.assertAlmostEqual(metrics["deviations"][30], 5.10100501, places=8)

    def test_each_confirmed_direction_counts_separately_and_future_events_are_excluded(self):
        events = [{"sourceId": key, "endDate": day, "direction": direction}
                  for key, day, direction in [("up1", "20261020", "up"), ("up2", "20261021", "up"),
                                              ("down", "20261022", "down"), ("future", "20261026", "up")]]
        metrics = MarketService._vector_metrics(vector(self.dates, [1] * 5),
            vector(self.dates, [0] * 5), BOARD_RULES[Board.MAIN], ordinary_events=events)
        self.assertEqual((metrics["up"], metrics["down"], metrics["same"]), (2, 1, 2))

    def test_severe_reset_is_inactive_before_effective_day_then_clears_old_events(self):
        event = {"sourceId": "old", "endDate": "20261022", "direction": "up"}
        stock, index = vector(self.dates, [10] * 5), vector(self.dates, [0] * 5)
        before = MarketService._vector_metrics(stock[:-1], index[:-1], BOARD_RULES[Board.MAIN],
            ordinary_events=[event], severe_reset_dates=["20261023"])
        self.assertEqual(before["up"], 1)
        self.assertGreater(before["deviations"][10], 40)
        effective = MarketService._vector_metrics(stock, index, BOARD_RULES[Board.MAIN],
            ordinary_events=[event], severe_reset_dates=["20261023"])
        self.assertEqual(effective["up"], 0)
        self.assertEqual(effective["deviations"], {3: 0, 10: 0, 30: 0})

    def test_ordinary_resume_resets_price_windows_but_keeps_confirmed_event_ledger(self):
        dates = ["20260921", "20260922", "20260925"]
        stock = vector(dates, [80, 10, 5])
        index = vector(dates, [0, 0, 0])
        metrics = MarketService._vector_metrics(
            stock, index, BOARD_RULES[Board.MAIN], price_reset_dates=["20260925"],
            ordinary_events=[{"sourceId": "ordinary-before-pause", "endDate": "20260922", "direction": "up"}],
        )
        self.assertEqual(metrics["deviations"], {3: 0.0, 10: 0.0, 30: 0.0})
        self.assertEqual((metrics["up"], metrics["down"]), (1, 0))

    def test_severe_resume_boundary_clears_price_windows_and_direction_counts(self):
        dates = ["20260921", "20260922", "20260925"]
        stock = vector(dates, [80, 10, 5])
        index = vector(dates, [0, 0, 0])
        metrics = MarketService._vector_metrics(
            stock, index, BOARD_RULES[Board.MAIN],
            price_reset_dates=["20260925"], severe_reset_dates=["20260925"],
            ordinary_events=[{"sourceId": "ordinary-before-pause", "endDate": "20260922", "direction": "up"}],
        )
        self.assertEqual(metrics["deviations"], {3: 0.0, 10: 0.0, 30: 0.0})
        self.assertEqual((metrics["up"], metrics["down"]), (0, 0))

    def test_event_end_date_leaves_ten_session_window_independently_of_start(self):
        dates = ["20261019", "20261020", "20261021", "20261022", "20261023",
                 "20261026", "20261027", "20261028", "20261029", "20261030", "20261102"]
        events = [{"sourceId": "edge", "startDate": "20261015", "endDate": "20261019", "direction": "up"}]
        stock, index = vector(dates, [1] * 11), vector(dates, [0] * 11)
        self.assertEqual(MarketService._vector_metrics(stock[:-1], index[:-1], BOARD_RULES[Board.MAIN], ordinary_events=events)["up"], 1)
        self.assertEqual(MarketService._vector_metrics(stock, index, BOARD_RULES[Board.MAIN], ordinary_events=events)["up"], 0)

    def test_unresolvable_fact_is_retained_for_unavailable_projection(self):
        metrics = MarketService._vector_metrics(vector(self.dates[-1:], [0]),
            vector(self.dates[-1:], [0]), BOARD_RULES[Board.MAIN], ordinary_events=[
                {"sourceId": "pending", "endDate": "20261023", "direction": ""}])
        self.assertEqual(len(metrics["unresolved_same_direction_events"]), 1)
        self.assertEqual(metrics["unresolved_same_direction_events"][0]["directionStatus"], "pending_verification")
        # Consumers must project unavailable; this diagnostic count alone is
        # explicitly NOT sufficient evidence for an exact count of zero.
