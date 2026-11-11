"""IPO boundary regression: calendar, paired returns, pages and facts."""
import unittest
import tempfile
import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from server.rules.listing import listing_applicability
from server.rules.catalog import BOARD_RULES, Board
from server.services.market_service import MarketService, PREDICTION_RULE_VERSION
from server.services.read_publications import ReadPublications
from server.data.publication_store import PublicationStore


DAYS = ["20261110", "20261111", "20261112", "20261113", "20261116", "20261117", "20261118"]


class ListingApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.service = MarketService()
        self.service.set_clock(lambda: datetime(2026, 11, 11, 18))
        self.service.client.trade_cal = Mock(return_value=SimpleNamespace(
            to_dict=lambda *_: [{"cal_date": day, "is_open": 1} for day in DAYS]))
        self.service._fetch_realtime_many = lambda *_: {}
        self.service.set_monitor_provider(lambda: {"items": [], "resetFacts": [], "dataQuality": {}})
        self.stock = {"ts_code": "688827.SH", "symbol": "688827", "name": "C钶锐",
                      "board": "科创板", "market": "SSE", "isST": False,
                      "list_date": DAYS[0], "list_status": "L", "priceLimitEnabled": True}

    def tearDown(self):
        self.service.close()

    def vectors(self, count):
        return ([{"date": day, "return": 200.0 if i == 0 else 10.0} for i, day in enumerate(DAYS[:count])],
                [{"date": day, "return": 1.0} for day in DAYS[:count]])

    def detail(self, count, use_realtime=False):
        stocks, indexes = self.vectors(count)
        day = DAYS[count - 1]
        calc = {**self.stock, "as_of_trade_date": day, "latest_close": 30, "stock_return_vector": stocks}
        index = {"as_of_trade_date": day, "latest_close": 1000, "index_return_vector": indexes}
        self.service._future_trade_dates = lambda latest, *_: [d for d in DAYS if d > latest.replace("-", "")]
        self.service._stock_rows = lambda: [self.stock]
        self.service._calc_repository = SimpleNamespace(available=True, get_calc=lambda _: calc, get_index=lambda _: index)
        return self.service._detail_from_repository(self.stock, calc, use_realtime=use_realtime)

    def test_all_boards_and_weekend_boundary(self):
        for board in ["主板", "创业板", "科创板"]:
            for i in range(5):
                with self.subTest(board=board, day=DAYS[i]):
                    self.assertFalse(listing_applicability(board, DAYS[0], DAYS[i], DAYS)["applicable"])
            self.assertEqual(listing_applicability(board, DAYS[0], DAYS[5], DAYS)["effectiveFrom"], DAYS[5])
            self.assertTrue(listing_applicability(board, DAYS[0], DAYS[5], DAYS)["applicable"])
        self.assertFalse(listing_applicability("北交所", DAYS[0], DAYS[0], DAYS)["applicable"])
        self.assertTrue(listing_applicability("北交所", DAYS[0], DAYS[1], DAYS)["applicable"])

    def test_holiday_sessions_not_calendar_day_offsets(self):
        days = ["20260930", "20261009", "20261012", "20261013", "20261014", "20261015"]
        self.assertFalse(listing_applicability("主板", days[0], days[4], days)["applicable"])
        self.assertEqual(listing_applicability("主板", days[0], days[5], days)["effectiveFrom"], days[5])

    def test_only_post_ipo_returns_and_matching_index_are_used(self):
        stocks, indexes = self.vectors(7)
        filtered, paired, meta, _ = self.service._rule_vectors_for_stock(self.stock, stocks, indexes)
        self.assertEqual([row["date"] for row in filtered], DAYS[5:])
        self.assertEqual([row["date"] for row in paired], DAYS[5:])
        self.assertEqual(len(stocks), 7)  # original source is unchanged
        metrics = self.service._vector_metrics(filtered, paired, BOARD_RULES[Board.STAR])
        self.assertAlmostEqual(metrics["deviations"][10], 121.0 - 102.01)
        self.assertEqual(metrics["same_direction_candidates"], [])
        self.assertEqual(meta["priceResetDates"], [])  # IPO eligibility is not an announcement/reset

    def test_first_effective_return_is_included_not_zeroed(self):
        detail = self.detail(6)
        self.assertTrue(detail["ruleApplicability"]["applicable"])
        self.assertEqual(detail["warnings"][2]["upValue"], "+9.00%")
        self.assertEqual(detail["warnings"][2]["actualWindow"], 1)
        self.assertEqual(detail["simulationBase"]["dates"], [DAYS[5]])
        self.assertEqual(detail["warnings"][1]["exact_count"], 0)  # IPO涨幅不制造普通公告事件

    def test_each_board_detail_starts_with_full_first_limited_return(self):
        for board, first_valid in [("主板", 6), ("创业板", 6), ("科创板", 6), ("北交所", 2)]:
            with self.subTest(board=board):
                self.stock["board"] = board
                self.assertTrue(all(w["availability"] == "not_applicable"
                                    for w in self.detail(first_valid - 1)["warnings"]))
                detail = self.detail(first_valid)
                self.assertEqual(detail["warnings"][2]["upValue"], "+9.00%")
                self.assertEqual(len(detail["simulationBase"]["dates"]), 1)

    def test_ipo_detail_keeps_quote_and_has_four_not_applicable_warnings(self):
        detail = self.detail(2)
        self.assertFalse(detail["priceLimitEnabled"])
        self.assertEqual(detail["currentPrice"], "30.00")
        self.assertEqual(detail["change"], "+10.00%")
        self.assertEqual(len(detail["warnings"]), 4)
        self.assertTrue(all(row["availability"] == "not_applicable" for row in detail["warnings"]))
        self.assertEqual(detail["stockState"]["marketRiskState"], "not-applicable")
        self.assertTrue(detail["dataQuality"]["isComplete"])
        self.assertEqual(detail["simulationBase"]["stock"], [])
        self.assertEqual(detail["alerts"], [])

    def test_current_day_five_and_next_day_six_have_distinct_applicability(self):
        detail = self.detail(5)
        self.assertFalse(detail["ruleApplicability"]["applicable"])
        self.assertTrue(detail["nextDayForecast"]["ruleApplicability"]["applicable"])
        self.assertTrue(detail["nextDayForecast"]["available"])
        self.assertEqual(detail["nextDayForecast"]["rules"], [])

    def test_calendar_failure_is_unknown_not_safe_and_is_not_cached(self):
        self.service.client.trade_cal.side_effect = RuntimeError("calendar unavailable")
        context = self.service._listing_applicability(self.stock, DAYS[1])
        self.assertIsNone(context["applicable"])
        self.assertEqual(context["reason"], "LISTING_CALENDAR_UNAVAILABLE")
        self.service.client.trade_cal.side_effect = None
        self.assertFalse(self.service._listing_applicability(self.stock, DAYS[1])["applicable"])

    def test_old_stocks_do_not_require_calendar_and_recent_dates_share_cache(self):
        self.service._listing_applicability({**self.stock, "list_date": "20000101"}, DAYS[0])
        self.service.client.trade_cal.assert_not_called()
        self.service._listing_applicability(self.stock, DAYS[0])
        self.service._listing_applicability(self.stock, DAYS[5])
        self.service.client.trade_cal.assert_called_once()

    def test_long_suspension_cannot_reintroduce_old_ipo_returns(self):
        stocks, indexes = self.vectors(6)
        stocks.append({"date": "20270301", "return": 1.0})
        indexes.append({"date": "20270301", "return": 0.0})
        filtered, _, _ = self.service._listing_vectors(self.stock, stocks, indexes)
        self.assertEqual(filtered[0]["date"], DAYS[5])

    def test_monitoring_fact_is_retained_during_ipo_period(self):
        for tone, expected in [("ordinary", "ordinary-warning"), ("severe-10d", "severe-monitoring")]:
            with self.subTest(tone=tone):
                fact = {"symbol": "688827", "riskTone": tone, "title": "风险提示公告",
                        "monitorStartDate": DAYS[0], "monitorEndDate": DAYS[5]}
                self.service.set_monitor_provider(lambda: {"items": [fact], "resetFacts": [], "dataQuality": {}})
                detail = self.detail(2)
                self.assertTrue(detail["monitoring"]["active"])
                self.assertEqual(detail["stockState"]["regulatoryState"], expected)
                self.assertFalse(detail["ruleApplicability"]["applicable"])

    def test_generation_excludes_all_unlimited_ipo_targets(self):
        stocks, indexes = self.vectors(2)
        self.service._stock_rows = lambda: [self.stock]
        self.service._calc_repository = SimpleNamespace(
            all_calculations=lambda: {self.stock["ts_code"]: {"as_of_trade_date": DAYS[1], "stock_return_vector": stocks}},
            all_indexes=lambda: {"000688.SH": {"as_of_trade_date": DAYS[1], "index_return_vector": indexes}})
        for scope in ("today", "next_day"):
            self.assertEqual(self.service._compute_prediction_from_repository(scope, target_trade_date=DAYS[1]), [])
            self.assertEqual(self.service._prediction_generation_stats[scope]["excludedBySpecialStock"], 1)

    def test_old_prediction_refresh_cannot_restore_ipo_cards(self):
        self.service._calc_repository = SimpleNamespace(available=True, get_prediction=lambda _: {
            "target_trade_date": DAYS[1], "items": [{**self.stock, "ruleKey": "severe_10d"}]})
        self.service._fetch_realtime_many = Mock(side_effect=AssertionError("excluded IPO must not fetch quotes"))
        self.assertEqual(self.service._refresh_existing_prediction_scope("today"), [])
        self.service._fetch_realtime_many.assert_not_called()

    def test_published_prediction_read_filters_preexisting_ipo_card(self):
        record = {"target_trade_date": DAYS[1], "updated_at": datetime(2026, 11, 11, 18),
                  "rule_version": PREDICTION_RULE_VERSION, "items": [{**self.stock, "ruleKey": "severe_10d"}]}
        self.service._calc_repository = SimpleNamespace(available=True)
        self.service._prediction_record = lambda scope, *_: record if scope == "today" else None
        self.service._prediction_state = lambda: {
            "todaySourceScope": "today", "targetTradeDate": DAYS[1], "previousTradeDate": DAYS[0],
            "nextTradeDate": DAYS[2], "phase": "post_close_confirmed", "realtimeRefreshAllowed": True}
        self.assertEqual(self.service.predictions("today")["items"], [])

    def test_s1_s2_preserve_not_applicable_while_updating_price(self):
        self.detail(2)
        with tempfile.TemporaryDirectory() as temp:
            store = PublicationStore(sqlite_path=str(Path(temp) / "reads.db"))
            reads = ReadPublications(self.service, store)
            identity, payload, _, _ = reads.capture({"page": "stock", "role": "current", "security_id": "688827.SH"})
            self.assertTrue(all(w["availability"] == "not_applicable" for w in payload["data"]["warnings"]))
            self.service._fetch_realtime_many = lambda *_: {
                "688827.SH": {"current": 33, "preClose": 30, "pctChg": 10, "updatedAt": "2026-11-11 14:00:00", "source": "腾讯"},
                "000688.SH": {"current": 1010, "preClose": 1000, "pctChg": 1, "updatedAt": "2026-11-11 14:00:00", "source": "腾讯"},
            }
            data, _ = reads.refresh(SimpleNamespace(dataset_id=identity, realtime_view_id="", role="current", target_date=DAYS[1]))
            self.assertEqual(data["currentPrice"], "33.00")
            self.assertEqual(data["result_state"], "fresh")
            self.assertTrue(all(w["availability"] == "not_applicable" for w in data["warnings"]))
            self.assertEqual(data["simulationBase"]["dates"], [])

    def test_realtime_first_effective_session_includes_return_from_prior_close(self):
        self.service.set_clock(lambda: datetime(2026, 11, 17, 14))
        self.service._fetch_realtime_many = lambda *_: {
            "688827.SH": {"current": 33, "preClose": 30, "pctChg": 10, "updatedAt": "2026-11-17 13:00:00", "source": "腾讯"},
            "000688.SH": {"current": 1010, "preClose": 1000, "pctChg": 1, "updatedAt": "2026-11-17 13:00:00", "source": "腾讯"},
        }
        detail = self.detail(5, use_realtime=True)
        self.assertTrue(detail["ruleApplicability"]["applicable"])
        self.assertEqual(detail["warnings"][2]["upValue"], "+9.00%")
        self.assertEqual(detail["simulationBase"]["dates"], [DAYS[5]])

    def test_missing_or_invalid_listing_metadata_does_not_assume_safe(self):
        for listing in (None, "", "20261301"):
            self.assertIsNone(self.service._listing_applicability({**self.stock, "list_date": listing}, DAYS[1])["applicable"])

    def test_serialized_detail_is_consumed_by_real_frontend_functions(self):
        for count in (2, 6):
            with self.subTest(count=count):
                detail = self.detail(count)
                payload = {"detail": detail, "rows": [{"date": DAYS[count], "stock": 0, "index": 0}]}
                script = ("global.Page=()=>{};const p=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                          "const s=require('./miniprogram/pages/stock/index');"
                          "process.stdout.write(JSON.stringify({warnings:s.presentWarnings(p.detail.warnings),"
                          "rows:s.calculateSimulationRows(p.rows,p.detail)}));")
                result = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[2],
                                        input=json.dumps(payload), text=True, capture_output=True, check=True)
                rendered = json.loads(result.stdout)
                if count == 2:
                    self.assertTrue(all(w["currentText"] == "暂不适用" for w in rendered["warnings"]))
                    self.assertEqual(rendered["rows"][0]["space"], "暂不适用")
                else:
                    self.assertEqual(rendered["rows"][0]["deviation10"], "+9.00%")
                    self.assertTrue(rendered["rows"][0]["space"].endswith("%"))


if __name__ == "__main__":
    unittest.main()
