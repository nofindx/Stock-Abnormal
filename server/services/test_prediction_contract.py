import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from server.services.intraday_state_store import IntradayStateStore
from server.services.market_service import MarketService, SHANGHAI_TZ


class IntradayStateStoreTests(unittest.TestCase):
    def test_instances_share_fallback_state_and_enforce_payload_limit(self):
        first = IntradayStateStore(redis_url="")
        second = IntradayStateStore(redis_url="")
        first.delete("today", "20261012")
        first.set("today", "20261012", {"updatedAt": "2026-10-12 10:00:00", "items": [{"predictionKey": "a"}]})
        self.assertEqual(second.get("today", "20261012")["items"][0]["predictionKey"], "a")
        with self.assertRaises(ValueError):
            first.set("today", "20261012", {"items": ["x" * IntradayStateStore.MAX_PAYLOAD_BYTES]})
        first.delete("today", "20261012")


class PredictionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.service = MarketService()
        self.service.client = SimpleNamespace(
            trade_cal=lambda _start, _end: SimpleNamespace(to_dict=lambda _orient: [
                {"cal_date": "20261012", "is_open": 1},
                {"cal_date": "20261013", "is_open": 1},
            ]),
            latest_trade_date=lambda: "20261009",
        )

    def tearDown(self):
        self.service.close()

    def test_phase_boundaries_are_recomputed_without_thirty_second_cache(self):
        cases = [
            ("08:59:59", "pre_open", False, "today"),
            ("09:00:00", "dataset_open_pending", False, "next_day"),
            ("09:14:59", "dataset_open_pending", False, "next_day"),
            ("09:15:00", "intraday", True, "next_day"),
            ("14:59:59", "intraday", True, "next_day"),
            ("15:00:00", "post_close_pending", False, "today"),
        ]
        for clock_text, phase, allowed, source in cases:
            value = datetime.fromisoformat(f"2026-10-12 {clock_text}").replace(tzinfo=SHANGHAI_TZ)
            self.service.set_clock(lambda value=value: value)
            state = self.service._prediction_state()
            self.assertEqual(state["phase"], phase, clock_text)
            self.assertEqual(state["realtimeRefreshAllowed"], allowed, clock_text)
            self.assertEqual(state["todaySourceScope"], source, clock_text)


class FixtureContractTests(unittest.TestCase):
    def test_fixture_contains_only_three_prediction_rules(self):
        path = Path(__file__).parents[1] / "fixtures" / "prediction_cases.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        keys = {item["ruleKey"] for item in payload["three_rules_same_stock"]["items"]}
        self.assertEqual(keys, {"ordinary_3d", "severe_10d", "severe_30d"})
        self.assertNotIn("same_direction_10d", keys)

    def test_special_stock_eligibility_is_excluded_from_prediction(self):
        self.assertFalse(MarketService._prediction_stock_eligible({"isST": True}, "20261012"))
        self.assertFalse(MarketService._prediction_stock_eligible({"name": "退市示例"}, "20261012"))
        self.assertFalse(MarketService._prediction_stock_eligible({"priceLimitEnabled": False}, "20261012"))
        self.assertFalse(MarketService._prediction_stock_eligible({"list_date": "20261012"}, "20261012"))
        self.assertTrue(MarketService._prediction_stock_eligible({"name": "正常股票", "list_date": "20200101"}, "20261012"))


class PredictionFailurePropagationTests(unittest.TestCase):
    class FailedRepository:
        available = True

        def get_prediction(self, scope, target_trade_date=None):
            return {
                "scope": scope,
                "target_trade_date": target_trade_date or "20261009",
                "base_trade_date": "20261009",
                "updated_at": datetime(2026, 10, 9, 15, 0, 0),
                "dataset_quote_updated_at": datetime(2026, 10, 9, 15, 0, 0),
                "items": [{"predictionKey": "600000.SH-ordinary_3d-20261009"}],
                "status": "ready",
                "data_stage": "formal",
                "data_quality": "confirmed",
                "dataset_quote_complete": True,
                "exclusion_stats": {},
            }

        def prediction_job_status(self, scope):
            return {"status": "failed", "last_error": f"{scope} 任务失败"}

    def test_failed_job_keeps_last_items_but_marks_dataset_unavailable(self):
        service = MarketService()
        service._calc_repository = self.FailedRepository()
        service._prediction_state = lambda: {
            "todaySourceScope": "today",
            "targetTradeDate": "20261009",
            "previousTradeDate": "20261008",
            "nextTradeDate": "20261012",
            "phase": "rest_day",
            "realtimeRefreshAllowed": False,
        }
        try:
            payload = service.predictions("today")
            self.assertTrue(payload["dataUnavailable"])
            self.assertEqual(payload["datasetStatus"], "failed")
            self.assertEqual(payload["error"], "today 任务失败")
            self.assertEqual(len(payload["items"]), 1)
        finally:
            service.close()
