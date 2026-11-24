"""2.0.4 regressions: real service paths, isolated quotes/status evidence."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from server.domain.contracts import TradeState
from server.services.market_service import MarketService, SHANGHAI_TZ
from server.services.realtime.coordinator import RealtimeCoordinator, _MemoryBackend, build_realtime_key
from server.services.realtime.validation import comparable_quotes, quote_time, usable_quote
from server.services.realtime_quote import RealtimeQuoteClient


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=SHANGHAI_TZ)


def quote(source="腾讯行情", **changes):
    return {**dict(current=10, preClose=10, pctChg=0, updatedAt="2026-11-20T11:30:00", source=source), **changes}


def frame(rows):
    return SimpleNamespace(to_dict=lambda *_args: deepcopy(rows))


class QuoteValidation204Tests(TestCase):
    def test_pairing_59_60_61_seconds_both_directions_and_offset_normalization(self):
        for seconds in (-61, -60, -59, 59, 60, 61):
            with self.subTest(seconds=seconds):
                stamp = (datetime(2026, 11, 20, 11, 30) + timedelta(seconds=seconds)).isoformat()
                self.assertEqual(comparable_quotes(quote(), quote(updatedAt=stamp)), abs(seconds) <= 60)
        self.assertTrue(comparable_quotes(quote(), quote(updatedAt="2026-11-20T03:30:00Z")))
        self.assertTrue(comparable_quotes(quote(updatedAt="2026-11-19T19:30:00-08:00"), quote()))

    def test_bad_time_source_and_cross_day_are_never_paired(self):
        for value in ("", "2026-11-20", "2026-11-20Txx:yy:zz", "2026-11-20T25:00:00",
                      "2026-11-31T11:30:00", "2026-11-20T11:30:00garbage"):
            with self.subTest(value=value):
                self.assertIsNone(quote_time(quote(updatedAt=value)))
                self.assertFalse(comparable_quotes(quote(), quote(updatedAt=value)))
        for other in (quote(source=""), quote(source="新浪行情"), quote(updatedAt="2026-11-19T11:30:00")):
            self.assertFalse(comparable_quotes(quote(), other))
        self.assertFalse(comparable_quotes(quote(source=""), quote(source="")))
        self.assertFalse(comparable_quotes(quote(), "malformed"))

    def test_age_is_not_pair_skew_or_cache_ttl(self):
        # Weekend and lunch retain the genuine latest quote date.
        weekend = datetime(2026, 11, 22, 12, 0, tzinfo=SHANGHAI_TZ)
        self.assertTrue(usable_quote(quote(), now=weekend, earliest_day="20261120"))
        self.assertFalse(usable_quote(quote(), now=NOW, earliest_day="20261123"))
        self.assertFalse(usable_quote(quote(updatedAt="2026-11-20T12:00:01"), now=NOW))
        for price in (0, -1, float("nan"), float("inf"), "bad"):
            self.assertFalse(usable_quote(quote(current=price), now=NOW))

    def test_invalid_primary_tries_remaining_sources_and_all_invalid_is_failure(self):
        invalids = [quote(current=0), quote(current=float("inf")), quote(updatedAt="2026-11-20"),
                    quote(updatedAt="2026-11-20Txx:yy:zz"), quote(preClose=0), quote(pctChg=float("nan")),
                    quote(updatedAt="2026-11-19T11:30:00"), quote(updatedAt="2026-11-20T12:00:01")]
        for invalid in invalids:
            with self.subTest(invalid=invalid):
                client = RealtimeQuoteClient()
                client._fetch_tencent = Mock(return_value={"000001.SZ": invalid})
                client._fetch_sina = Mock(return_value={"000001.SZ": quote(source="新浪行情", current=11)})
                client._fetch_eastmoney = Mock()
                result = client.fetch_many(["000001.SZ"], now=NOW, earliest_days={"000001.SZ": "20261120"})
                self.assertEqual(result["000001.SZ"]["current"], 11)
                client._fetch_sina.assert_called_once_with(["000001.SZ"])
                client._fetch_eastmoney.assert_not_called()
                self.assertEqual(client.health_stats()["sources"]["tencent"]["failures"], 1)
        client._fetch_sina.return_value = {"000001.SZ": quote(current=0)}
        client._fetch_eastmoney.return_value = {"000001.SZ": quote(source="东方财富行情", current=12)}
        self.assertEqual(client.fetch_many(["000001.SZ"], now=NOW)["000001.SZ"]["current"], 12)
        client._fetch_eastmoney.return_value = {}
        self.assertEqual(client.fetch_many(["000001.SZ"], now=NOW), {})

    def test_partial_provider_batch_does_not_replace_valid_primary_and_eastmoney_isolates_errors(self):
        client = RealtimeQuoteClient()
        client._fetch_tencent = Mock(return_value={"000001.SZ": quote(), "399107.SZ": quote(current=0)})
        client._fetch_sina = Mock(return_value={"399107.SZ": quote(source="新浪行情", current=100)})
        client._fetch_eastmoney = Mock()
        result = client.fetch_many(["000001.sz", "399107.SZ", "000001.SZ"], now=NOW)
        self.assertEqual(result["000001.SZ"]["source"], "腾讯行情")
        client._fetch_sina.assert_called_once_with(["399107.SZ"])
        self.assertFalse(comparable_quotes(result["000001.SZ"], result["399107.SZ"]))
        client = RealtimeQuoteClient()
        response = Mock()
        response.read.return_value = b'{"data":{"f43":10,"f60":10,"f86":1795145400}}'
        with patch("server.services.realtime_quote.urlopen", side_effect=[OSError("first failed"), response]):
            self.assertIn("399107.SZ", client._fetch_eastmoney(["000001.SZ", "399107.SZ"]))

    def test_valid_fallback_is_cached_and_bad_cached_record_does_not_block_fallback(self):
        service = MarketService()
        self.addCleanup(service.close)
        service.set_clock(lambda: NOW)
        service._realtime_coordinator = RealtimeCoordinator(backend=_MemoryBackend())
        service._realtime_coordinator._redis = None
        client = service._realtime
        client._fetch_tencent = Mock(return_value={"000001.SZ": quote(current=0)})
        client._fetch_sina = Mock(return_value={"000001.SZ": quote(source="新浪行情", current=11)})
        client._fetch_eastmoney = Mock()
        with patch("server.services.market_service.quote_bucket", return_value="test-bucket"):
            key = build_realtime_key("quote", "000001.SZ", "unfrozen", "test-bucket")
            with patch.dict("os.environ", {"SOURCE_POLICY_HASH": "unfrozen"}):
                first = service._fetch_realtime_many(["000001.SZ"], {"000001.SZ": "20261120"})
                self.assertEqual(first, service._fetch_realtime_many(["000001.SZ"]))
                self.assertEqual(client._fetch_sina.call_count, 1)
                service._realtime_coordinator._memory.values[key] = (float("inf"), quote(updatedAt="2026-11-19T11:30:00"))
                second = service._fetch_realtime_many(["000001.SZ"], {"000001.SZ": "20261120"})
                self.assertEqual(second["000001.SZ"]["current"], 11)
                self.assertEqual(client._fetch_sina.call_count, 2)


class StockDetail204Tests(TestCase):
    def setUp(self):
        self.code = "000001.SZ"
        self.stock = {"ts_code": self.code, "symbol": "000001", "name": "测试股票", "market": "SZSE",
                      "board": "主板", "list_status": "L", "list_date": "20000101"}
        self.calc = {**self.stock, "latest_close": 10, "as_of_trade_date": "20261119",
                     "stock_return_vector": [{"date": "20261119", "return": 1}]}
        self.index = {"latest_close": 100, "as_of_trade_date": "20261119",
                      "index_return_vector": [{"date": "20261119", "return": 0}]}
        self.events, self.facts = [], []
        self.service = MarketService()
        self.addCleanup(self.service.close)
        self.service.set_clock(lambda: NOW)
        self.service._future_trade_dates = lambda *_: ["20261123"]
        self.service.client.suspend_d = Mock(side_effect=lambda *_args, **_kw: frame(self.events))
        self.service._calc_repository = SimpleNamespace(available=True, get_calc=lambda _: deepcopy(self.calc),
                                                       get_index=lambda _: deepcopy(self.index))
        self.service._fetch_realtime_many = Mock(return_value={})
        self.service.set_monitor_provider(lambda: {"items": deepcopy(self.facts), "resetFacts": [],
            "unresolvedFacts": [], "dataQuality": {"status": "complete"}})

    def test_four_trading_states_quote_failure_and_monitoring_are_independent(self):
        self.facts = [{"symbol": "000001", "sourceId": "severe", "riskTone": "severe-10d",
                       "monitorStartDate": "20261116", "monitorEndDate": "20261127"}]
        for events, expected in (([], "normal"),
                                 ([{"ts_code": self.code, "trade_date": "20261120", "suspend_type": "S"}], "suspended"),
                                 ([{"ts_code": self.code, "trade_date": "20261120", "suspend_type": "R"}], "normal")):
            with self.subTest(state=expected, events=events):
                self.events = events
                result = self.service.detail(self.code)
                self.assertEqual(result["stockState"]["tradingState"], expected)
                self.assertEqual(result["stockState"]["regulatoryState"], "severe-monitoring")
                self.assertEqual(result["dataQuality"]["quoteAvailable"], False)
                self.assertEqual(len(result["warnings"]), 4)
        self.calc.update(list_status="D", delist_date="20261120")
        # No index or numeric base must not hide a confirmed termination.
        self.calc["stock_return_vector"] = []
        self.calc["as_of_trade_date"] = ""
        result = self.service.detail(self.code)
        self.assertEqual(result["stockState"]["tradingState"], "terminated")
        self.assertFalse(result["realtimeRefreshAllowed"])
        self.assertEqual(result["stockState"]["regulatoryState"], "severe-monitoring")
        self.calc.update(list_status="", delist_date="")
        self.assertEqual(self.service.detail(self.code)["stockState"]["tradingState"], "unknown")

    def test_status_source_failure_does_not_invent_a_halt(self):
        self.service.client.suspend_d.side_effect = RuntimeError("status unavailable")
        result = self.service.detail(self.code)
        self.assertEqual(result["stockState"]["tradingState"], "unknown")
        self.assertEqual(result["stockState"]["marketDataState"], "complete")

    def test_intraday_halt_interval_is_reevaluated_from_pinned_evidence(self):
        self.events = [{"ts_code": self.code, "trade_date": "20261120",
                        "suspend_type": "S", "suspend_timing": "11:00-13:00"}]
        self.assertEqual(self.service.detail(self.code)["stockState"]["tradingState"], "suspended")
        evidence = self.service.detail(self.code)["stockState"]["tradingEvidence"]
        self.service.set_clock(lambda: datetime(2026, 11, 20, 13, 0, tzinfo=SHANGHAI_TZ))
        self.service.client.suspend_d = Mock(side_effect=AssertionError("must use pinned status evidence"))
        state, _, _ = self.service._trading_state(self.stock, self.calc, evidence=evidence)
        self.assertEqual(state, TradeState.NORMAL)

    def test_valid_stock_only_quote_updates_price_but_keeps_confirmed_calculations(self):
        before = self.service.detail(self.code, use_realtime=False)
        self.service._fetch_realtime_many.return_value = {
            self.code: quote(current=11, pctChg=10), "399107.SZ": quote(updatedAt="2026-11-20Txx:yy:zz", current=100)}
        after = self.service.detail(self.code)
        self.assertEqual(after["currentPrice"], "11.00")
        self.assertFalse(after["dataQuality"]["realtime"])
        self.assertEqual(before["calculationInput"], after["calculationInput"])
        self.assertEqual(before["warnings"], after["warnings"])

    def test_all_day_halt_and_old_halt_do_not_become_normal_after_hours(self):
        self.events = [{"ts_code": self.code, "trade_date": "20261120",
                        "suspend_type": "S", "suspend_timing": "09:30-15:00"}]
        self.service.set_clock(lambda: datetime(2026, 11, 20, 16, 0, tzinfo=SHANGHAI_TZ))
        self.assertEqual(self.service.detail(self.code)["stockState"]["tradingState"], "suspended")
        self.events[0]["suspend_timing"] = "09:30-11:30,13:00-15:00"
        self.assertEqual(self.service.detail(self.code)["stockState"]["tradingState"], "suspended")
        self.events[0]["trade_date"] = "20261119"
        self.assertEqual(self.service.detail(self.code)["stockState"]["tradingState"], "suspended")
        self.calc["as_of_trade_date"] = "20261102"
        self.calc["stock_return_vector"] = [{"date": "20261102", "return": 1}]
        self.events = [{"ts_code": self.code, "trade_date": "20261103", "suspend_type": "S"}]
        self.assertEqual(self.service.detail(self.code)["stockState"]["tradingState"], "suspended")
        self.assertIn("ts_code", self.service.client.suspend_d.call_args.kwargs)

    def test_inactive_pool_marker_needs_confirmed_delisting_date(self):
        self.calc.update(list_status="D", delist_date="")
        self.service.client.delisted_stock = Mock(return_value=frame([
            {"ts_code": self.code, "list_status": "D", "delist_date": "20261119"}]))
        self.assertEqual(self.service.detail(self.code)["stockState"]["tradingState"], "terminated")
        self.service.client.delisted_stock.return_value = frame([])
        self.assertEqual(self.service.detail(self.code)["stockState"]["tradingState"], "unknown")

    def test_initial_stage_rejects_invalid_or_unpaired_quotes_before_writing(self):
        self.service._stock_rows = lambda force=False: [self.stock]
        self.service.client.trade_cal = lambda *_: frame([{"cal_date": "20261120", "is_open": 1}])
        repo = self.service._calc_repository
        repo.ensure_schema = Mock()
        repo.all_calculations = lambda: {self.code: self.calc}
        repo.all_indexes = lambda: {"399107.SZ": self.index}
        repo.upsert_market = Mock()
        stock_quote = quote(updatedAt="2026-11-20T15:00:00")
        index_quote = quote(updatedAt="2026-11-20T15:00:00", current=100, preClose=100)
        self.service._realtime.fetch_many = Mock()
        after_close = datetime(2026, 11, 20, 15, 2, tzinfo=SHANGHAI_TZ)
        with patch("server.services.market_service.datetime", wraps=datetime) as clock:
            clock.now.return_value = after_close
            for stock_changes, index_changes in (
                ({"updatedAt": "2026-11-20Txx:yy:zz"}, {}),
                ({"updatedAt": "2026-11-20"}, {}),
                ({"updatedAt": "2026-11-20T15:01:01"}, {}),
                ({"source": "新浪行情"}, {}),
                ({}, {"current": 0}), ({}, {"updatedAt": "2026-11-20Txx:yy:zz"})):
                with self.subTest(stock=stock_changes, index=index_changes):
                    self.service._realtime.fetch_many.return_value = {
                        self.code: {**stock_quote, **stock_changes}, "399107.SZ": {**index_quote, **index_changes}}
                    self.assertFalse(self.service.update_initial_data()["updated"])
                    repo.upsert_market.assert_not_called()
            self.service._realtime.fetch_many.return_value = {
                self.code: stock_quote, "399107.SZ": index_quote}
            self.assertTrue(self.service.update_initial_data()["updated"])
            self.assertEqual(repo.upsert_market.call_count, 1)
            self.assertEqual(repo.upsert_market.call_args.args[1][0]["quote_updated_at"], "2026-11-20 15:00:00")

    def test_monitoring_order_overlay_dedup_expiry_and_future_period(self):
        self.facts = [{"symbol": "000001", "riskTone": tone, "sourceId": tone,
                       "monitorStartDate": "20261116", "monitorEndDate": "20261120"}
                      for tone in ("ordinary", "severe-10d", "delisting-warning", "severe-30d")]
        self.facts += [deepcopy(self.facts[1]), {**self.facts[0], "sourceId": "expired", "monitorEndDate": "20261119"},
                      {**self.facts[1], "sourceId": "future", "announcementDate": "20261120",
                       "monitorStartDate": "20261123", "monitorEndDate": "20261204"}]
        result = self.service.detail(self.code, use_realtime=False)
        records = result["monitoringRecords"]
        self.assertEqual([x["sourceId"] for x in records],
                         ["delisting-warning", "severe-30d", "severe-10d", "ordinary", "future"])
        self.assertEqual(result["monitoring"]["riskTone"], "delisting-warning")
        self.assertFalse(records[-1]["active"])
        self.assertTrue(records[-1]["confirmedPendingStart"])
        self.service.set_clock(lambda: datetime(2026, 11, 21, tzinfo=SHANGHAI_TZ))
        result = self.service.detail(self.code, use_realtime=False)
        self.assertIsNone(result["monitoring"])
        self.assertEqual([x["sourceId"] for x in result["monitoringRecords"]], ["future"])
