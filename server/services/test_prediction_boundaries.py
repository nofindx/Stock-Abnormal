"""Signed target-day boundaries must survive computation, publication and refresh."""
import json
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from server.core.abnormal_rules import BOARD_RULES, Board
from server.services.market_service import MarketService


class SignedPredictionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parents[1] / "fixtures/aoya_prediction_boundary_20261103.json").read_text())
        self.service = MarketService()
        self.service.set_clock(lambda: datetime(2026, 11, 3, 14, 0))
        self.stock = self.fixture["stock"]
        self.index = self.fixture["index"]
        self.quotes = {}
        for code, vector in ((self.fixture["ts_code"], self.stock), (self.fixture["index_code"], self.index)):
            self.quotes[code] = {
                "current": 100 * (1 + vector[-1]["return"] / 100), "preClose": 100,
                "pctChg": vector[-1]["return"], "source": "腾讯行情",
                "updatedAt": "2026-11-03T14:00:00",
            }
        stock_row = {"ts_code": self.fixture["ts_code"], "symbol": "300949", "name": "奥雅股份",
                     "board": "创业板", "market": "SZSE", "isST": False}
        self.service._stock_rows = lambda: [stock_row]
        self.service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {self.fixture["ts_code"]: {
                "as_of_trade_date": "20261103", "latest_close": self.quotes[self.fixture["ts_code"]]["current"],
                "stock_return_vector": self.stock,
            }},
            all_indexes=lambda: {self.fixture["index_code"]: {
                "as_of_trade_date": "20261103", "latest_close": self.quotes[self.fixture["index_code"]]["current"],
                "index_return_vector": self.index,
            }},
        )
        self.service._rule_vectors_for_stock = lambda _stock, s, i: (
            s, i, {"ordinaryEvents": [], "ruleFactsQuality": {"status": "available"}}, None)
        self.service._monitored_rule_codes = lambda *_: {}
        self.service._realtime = SimpleNamespace(fetch_many=lambda _codes: deepcopy(self.quotes))
        self.service._fetch_realtime_many = lambda _codes: deepcopy(self.quotes)

    def tearDown(self):
        self.service.close()

    def ten_day_card(self, cards):
        return next(x for x in cards if x["ruleKey"] == "severe_10d")

    def test_aoya_inverse_matches_negative_boundary(self):
        boundary = self.service._required_up_percent(self.stock, self.index, 10, 100, "today", 10)
        self.assertAlmostEqual(boundary, -0.23908372774297248, places=10)
        self.assertEqual(self.service._prediction_trigger_fields(boundary)["triggerValue"], "-0.24%")
        # A negative upward boundary is not a downward warning or an achieved trigger.
        self.assertLess(self.stock[-1]["return"], boundary)
        metrics = self.service._vector_metrics(self.stock, self.index, BOARD_RULES[Board.CHINEXT])
        self.assertEqual(round(metrics["up_deviations"][10], 2), 99.03)
        self.assertFalse(metrics["up_deviations"][10] >= 100)

    def test_generation_and_both_realtime_paths_keep_same_signed_boundary(self):
        base = self.service._compute_prediction_from_repository("today", target_trade_date="20261103")
        self.service._calc_repository.get_prediction = lambda *_: {
            "target_trade_date": "20261103", "items": [self.ten_day_card(base)]}
        realtime_generated = self.service._compute_prediction_from_repository("today", use_realtime=True, target_trade_date="20261103")
        refreshed = self.service._refresh_existing_prediction_scope("today", "today")
        for name, cards in (("generation", base), ("generation-with-quotes", realtime_generated), ("fixed-pool-refresh", refreshed)):
            with self.subTest(path=name):
                card = self.ten_day_card(cards)
                self.assertEqual(card["triggerValue"], self.fixture["expectedTriggerValue"])
                self.assertEqual(card["triggerCondition"], "涨幅达 -0.24%")
                self.assertEqual(card["trigger"], card["triggerCondition"])
                self.assertEqual(card["triggered"], self.fixture["expectedTriggered"])
                self.assertEqual(card["cardTone"], "")

    def test_actual_return_crossing_negative_boundary_controls_trigger(self):
        base = self.service._compute_prediction_from_repository("today", target_trade_date="20261103")
        self.service._calc_repository.get_prediction = lambda *_: {
            "target_trade_date": "20261103", "items": [self.ten_day_card(base)]}
        # Test both sides of the unrounded inverse using the same index snapshot.
        boundary = self.service._required_up_percent(self.stock, self.index, 10, 100, "today", 10)
        for change, triggered in ((boundary - .001, False), (boundary + .001, True), (0.0, True)):
            with self.subTest(change=change):
                self.quotes[self.fixture["ts_code"]].update(current=100 * (1 + change / 100), pctChg=change)
                card = self.ten_day_card(self.service._refresh_existing_prediction_scope("today", "today"))
                self.assertEqual(card["triggerValue"], "-0.24%")
                self.assertEqual(card["triggered"], triggered)
                self.assertEqual(card["cardTone"], "triggered" if triggered else "")

    def test_next_day_scope_and_reset_boundary_remain_distinct(self):
        today = self.service._required_up_percent(self.stock, self.index, 10, 100, "today", 10)
        following = self.service._required_up_percent(self.stock, self.index, 10, 100, "next_day", 10)
        self.assertNotAlmostEqual(today, following)
        reset = self.service._required_up_percent(self.stock, self.index, 10, 100, "today", 10, "20261103", 30)
        self.assertIsNone(reset)
        self.assertEqual(self.service._prediction_trigger_fields(reset)["triggerValue"], "--")

    def test_negative_boundary_also_survives_next_day_inverse(self):
        stock = [{"date": "20261102", "return": 21.0}]
        index = [{"date": "20261102", "return": 0.0}]
        boundary = self.service._required_up_percent(stock, index, 3, 20, "next_day", 3)
        self.assertAlmostEqual(boundary, (1.2 / 1.21 - 1) * 100)
        self.assertLess(boundary, 0)

    def test_all_prediction_horizons_use_n_minus_one_known_sessions(self):
        """The target session must never be counted as a known stock session.

        This table is deliberately independent of the production helper: each
        expected value is the compound inverse for exactly N-1 prior sessions.
        It protects all three deviation horizons and both forecast scopes from
        regressing to the old N+1-day next-day calculation.
        """
        dates = [f"2026-10-{day:02d}" for day in range(1, 31)]
        stock = [{"date": day, "return": 1.0} for day in dates]
        index = [{"date": day, "return": 0.0} for day in dates]

        for horizon, target in ((3, 20.0), (10, 100.0), (30, 200.0)):
            for scope in ("today", "next_day"):
                with self.subTest(horizon=horizon, scope=scope):
                    if scope == "today":
                        # The latest index return belongs to the target day;
                        # only the preceding N-1 stock returns are known.
                        known = horizon - 1
                    else:
                        # The target day is after the latest vector day.
                        known = horizon - 1
                    stock_total = (1.01 ** known - 1.0) * 100.0
                    expected = ((1.0 + target / 100.0) / (1.0 + stock_total / 100.0) - 1.0) * 100.0
                    actual = MarketService._required_up_percent(
                        stock, index, horizon, target, scope, horizon,
                    )
                    self.assertAlmostEqual(actual, expected, places=8)

    def test_downward_inverse_preserves_positive_return_boundary(self):
        stock = [{"date": "20261102", "return": -25.0}, {"date": "20261103", "return": 10.0}]
        index = [{"date": x["date"], "return": 0.0} for x in stock]
        boundary = self.service._required_down_percent(stock, index, 3, -20, "today", 3)
        self.assertAlmostEqual(boundary, (.8 / .75 - 1) * 100)
        self.assertGreater(boundary, 0)

    def test_trigger_format_handles_positive_zero_negative_and_unavailable(self):
        for number, expected in ((2.35, "+2.35%"), (0, "+0.00%"), (-.00001, "+0.00%"), (-.24, "-0.24%"), (None, "--")):
            with self.subTest(number=number):
                fields = self.service._prediction_trigger_fields(number)
                self.assertEqual(fields["triggerValue"], expected)
                self.assertEqual(fields["trigger"], fields["triggerCondition"])

    def test_signed_prediction_sort_does_not_treat_negative_value_as_zero(self):
        cards = [{"ruleKey": "severe_10d", "triggerValue": value} for value in ("+2.00%", "+0.00%", "-0.24%")]
        self.assertEqual([x["triggerValue"] for x in self.service._sort_prediction_items(cards)], ["-0.24%", "+0.00%", "+2.00%"])


if __name__ == "__main__":
    unittest.main()
