"""A next-day ordinary-event candidate uses the two known sessions in its 3-day window."""
import json
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from server.services.market_service import MarketService


class SameDirectionForecastBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parents[1] / "fixtures/huayang_same_direction_next_day_20261104.json").read_text())
        self.service = MarketService()
        self.service.set_clock(lambda: datetime(2026, 11, 3, 16, 20))
        self.stock_vector = self.fixture["stock"]
        self.index_vector = self.fixture["index"]
        self.stock = {"ts_code": self.fixture["ts_code"], "symbol": "301502", "name": "华阳智能",
                      "board": "创业板", "market": "SZSE", "isST": False}
        self.meta = {
            "ordinaryEvents": self.fixture["ordinaryEvents"],
            "ordinaryResetDates": self.fixture["ordinaryResetDates"],
            "severeResetDate": self.fixture["severeResetDate"],
            "severeResetDates": self.fixture["severeResetDates"],
            "ruleFactsQuality": {"status": "available"},
        }
        self.service._stock_rows = lambda: [self.stock]
        self.service._rule_vectors_for_stock = lambda _stock, stock, index: (stock, index, deepcopy(self.meta), None)
        self.service._monitored_rule_codes = lambda *_: {}
        self.service._calc_repository = SimpleNamespace(
            available=True,
            all_calculations=lambda: {self.stock["ts_code"]: {
                "as_of_trade_date": "20261103", "latest_close": 1.0,
                "stock_return_vector": self.stock_vector,
            }},
            all_indexes=lambda: {self.fixture["index_code"]: {
                "as_of_trade_date": "20261103", "latest_close": 1.0,
                "index_return_vector": self.index_vector,
            }},
        )
        self.quotes = {
            self.fixture["ts_code"]: {"current": 1.0858369098712446, "preClose": 1.0,
                "pctChg": 8.58369098712446, "source": "腾讯行情", "updatedAt": "2026-11-03T16:14:00"},
            self.fixture["index_code"]: {"current": 0.9748002497472418, "preClose": 1.0,
                "pctChg": -2.5199750252758224, "source": "腾讯行情", "updatedAt": "2026-11-03T16:14:00"},
        }
        self.service._realtime = SimpleNamespace(fetch_many=lambda _codes: deepcopy(self.quotes))
        self.service._fetch_realtime_many = lambda _codes: deepcopy(self.quotes)

    def tearDown(self):
        self.service.close()

    def card(self, items):
        return next(item for item in items if item.get("ruleKey") == "same_direction_10d")

    def test_next_day_three_day_counter_boundary_uses_only_two_prior_sessions(self):
        required = self.service._required_up_percent(
            self.stock_vector, self.index_vector, 3, 30.0,
            forecast_scope="next_day", rule_window=3,
            rule_reset_date="20261027", reset_window=3,
        )
        self.assertAlmostEqual(required, 14.62996092758939, places=10)
        self.assertEqual(self.service._prediction_trigger_fields(required)["triggerValue"], self.fixture["expectedTriggerValue"])

    def test_generation_and_realtime_refresh_use_the_same_correct_boundary(self):
        dataset = self.service._compute_prediction_from_repository("next_day", target_trade_date=self.fixture["targetTradeDate"])
        self.assertEqual(self.card(dataset)["deviation"], "10日 +2次")
        self.assertEqual(self.card(dataset)["triggerValue"], self.fixture["expectedTriggerValue"])
        self.assertFalse(self.card(dataset)["triggered"])
        self.service._calc_repository.get_prediction = lambda *_: {
            "target_trade_date": self.fixture["targetTradeDate"],
            "items": [deepcopy(self.card(dataset))],
        }
        refreshed = self.service._refresh_existing_prediction_scope("next_day", "next_day")
        card = self.card(refreshed)
        self.assertEqual(card["deviation"], "10日 +2次")
        self.assertEqual(card["triggerValue"], self.fixture["expectedTriggerValue"])
        self.assertFalse(card["triggered"])


if __name__ == "__main__":
    unittest.main()
