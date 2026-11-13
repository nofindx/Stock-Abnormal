"""HTTP regressions for actual authorized reads, not token labels alone."""
import json
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch
from server import app
from server.data.publication_store import PublicationStore
from server.services.context_service import ContextService
from server.services.read_publications import ReadPublications
from server.services.read_refresh_jobs import ReadRefreshJobs
from server.services import test_production_regression as regression


class ReadApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = regression.ProductionRegressionTests()
        self.fixture.setUp()
        self.service = self.fixture.service
        self.service._future_trade_dates = lambda *_: ["20261102"]
        self.body = {"items": [{"ts_code": "002724.SZ", "ruleKey": "severe_10d", "triggered": True}],
                     "tradeDate": "20261030", "targetTradeDate": "20261030"}
        self.service.predictions = lambda *_: deepcopy(self.body)
        self.temp = tempfile.TemporaryDirectory()
        self.store = PublicationStore(sqlite_path=str(Path(self.temp.name) / "reads.db"))
        self.reads = ReadPublications(self.service, self.store)
        self.jobs = ReadRefreshJobs(self.reads, self.store)
        self.contexts = ContextService("test-only-signing-secret-32-bytes-minimum", store=self.store)
        self.patches = patch.multiple(app, SERVICE=self.service, READS=self.reads, READ_JOBS=self.jobs, CONTEXTS=self.contexts)
        self.patches.start()
        self.ready_patch = patch.object(app, "_realtime_ready", return_value=True)
        self.ready_patch.start()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), app.ApiHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.jobs.close()
        self.ready_patch.stop()
        self.patches.stop()
        self.fixture.tearDown()
        self.temp.cleanup()

    def call(self, path, values, method="GET"):
        url = f"http://127.0.0.1:{self.http.server_port}{path}"
        data = None
        if method == "GET":
            url += "?" + urlencode(values)
        else:
            data = json.dumps(values).encode()
        try:
            response = urlopen(Request(url, data=data, method=method, headers={"Content-Type": "application/json"}), timeout=5)
        except HTTPError as exc:
            response = exc
        return response.status, json.loads(response.read())

    def context(self, page="prediction", code="", entry="entry-a", role="current"):
        status, response = self.call("/api/v2/context", {"page": page, "role": role, "security_id": code, "entry_id": entry})
        self.assertEqual(status, 200)
        return response["data"]

    def test_monitor_trims_response_only_and_preserves_two_facts_and_pdf(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        snapshot = {"items": [
            {"ts_code": "A", "sourceId": "ordinary", "pdfAvailable": True, "sourceUrl": "original"},
            {"ts_code": "A", "sourceId": "severe", "pdfAvailable": True, "sourceUrl": "original"}],
            "resetFacts": [{"evidence": "keep internally"}], "unresolvedFacts": [{"reason": "pending"}],
            "dataQuality": {"status": "partial"}, "updatedAt": "2026-11-13 00:10:00"}
        original = deepcopy(snapshot)
        monitor = SimpleNamespace(read=lambda *_: snapshot, create_pdf_token=lambda item: item["sourceId"])
        with patch.object(app, "OFFICIAL_MONITOR", monitor), patch.object(
                self.service, "latest_stock_returns", Mock(return_value={"A": {"value": 2, "tradeDate": "20261112"}})) as get:
            status, response = self.call("/api/monitor", {"type": "all"})
            self.assertEqual(status, 200)
            body = response["data"]
            self.assertNotIn("resetFacts", body)
            self.assertNotIn("unresolvedFacts", body)
            self.assertEqual(len(body["items"]), 2)
            self.assertEqual([item["marketChange"] for item in body["items"]], [2, 2])
            self.assertTrue(all("/api/monitor/announcement?token=" in item["sourceUrl"] for item in body["items"]))
            self.assertEqual(body["dataQuality"], snapshot["dataQuality"])
            self.assertEqual(get.call_count, 1)
        self.assertEqual(snapshot, original)
        with patch.object(app, "OFFICIAL_MONITOR", monitor), patch.object(
                self.service, "latest_stock_returns", side_effect=RuntimeError("database unavailable")):
            status, response = self.call("/api/monitor", {})
            self.assertEqual(status, 200)
            self.assertEqual(len(response["data"]["items"]), 2)
            self.assertNotIn("marketChange", response["data"]["items"][0])

    def test_old_token_reads_saved_dataset_after_latest_changes(self):
        context = self.context()
        self.assertEqual(len(context["dataset_id"]), 64)
        self.assertEqual(context["pool_count"], 1)
        self.body["items"] = []
        status, result = self.call("/api/v2/predictions", {"read_token": context["read_token"]["value"], "role": "current"})
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["items"][0]["ts_code"], "002724.SZ")
        second = self.context(entry="entry-b")
        self.assertNotEqual(second["dataset_id"], context["dataset_id"])

    def test_token_cannot_change_role_or_stock_scope(self):
        context = self.context()
        token = context["read_token"]["value"]
        self.assertEqual(self.call("/api/v2/predictions", {"read_token": token, "role": "next"})[0], 403)
        self.assertEqual(self.call("/api/v2/stocks/detail", {"read_token": token, "security_id": "002724.SZ"})[0], 403)

    def test_expired_saved_view_returns_409_instead_of_latest(self):
        context = self.context()
        self.store.clock = lambda: 99999999999
        status, response = self.call("/api/v2/predictions", {"read_token": context["read_token"]["value"], "role": "current"})
        self.assertEqual(status, 409)
        self.assertEqual(response["code"], 40902)

    def test_stock_s1_and_s2_use_same_scope_and_support_off_hours(self):
        context = self.context("stock", "600825.SH")
        token = context["read_token"]["value"]
        status, response = self.call("/api/v2/stocks/detail", {"read_token": token, "security_id": "600825.SH"})
        self.assertEqual(status, 200)
        self.assertTrue(response["data"]["dataQuality"]["isComplete"])
        status, response = self.call("/api/v2/realtime/refresh", {"read_token": token, "page": "stock", "security_id": "600825.SH"}, "POST")
        self.assertEqual(status, 200)
        self.assertEqual(response["data"]["refreshStatus"], "fallback")
        self.assertEqual(response["data"]["result_state"], "stale")
        self.assertEqual(response["data"]["dataset_id"], context["dataset_id"])

    def test_stock_token_cannot_read_or_refresh_another_security(self):
        token = self.context("stock", "600825.SH")["read_token"]["value"]
        self.assertEqual(self.call("/api/v2/stocks/detail", {
            "read_token": token, "security_id": "002724.SZ"})[0], 403)
        self.assertEqual(self.call("/api/v2/realtime/refresh", {
            "read_token": token, "page": "stock", "security_id": "002724.SZ"}, "POST")[0], 403)

    def test_same_entry_pins_first_context_and_rejects_changed_scope(self):
        first = self.context()
        self.body["items"] = []
        self.assertEqual(self.context(), first)
        status, result = self.call("/api/v2/context", {
            "page": "prediction", "role": "next", "entry_id": "entry-a"})
        self.assertEqual((status, result["code"]), (409, 40901))

    def test_modified_token_and_expired_token_never_return_current_body(self):
        token = self.context()["read_token"]["value"]
        invalid = token[:-3] + ("aaa" if token[-3:] != "aaa" else "bbb")
        status, result = self.call("/api/v2/predictions", {"read_token": invalid, "role": "current"})
        self.assertEqual((status, result["code"], result["data"]), (409, 40902, None))
        self.contexts.clock = lambda: 99999999999
        status, result = self.call("/api/v2/predictions", {"read_token": token, "role": "current"})
        self.assertEqual((status, result["code"], result["data"]), (409, 40902, None))

    def test_s2_pins_s1_pool_basis_and_keeps_old_s1_read_immutable(self):
        context = self.context()
        token = context["read_token"]["value"]
        self.body["items"] = []  # A newer publication must not replace S1.
        self.service._detail_realtime_allowed = lambda: True

        def refresh(service, *_args):
            pinned = service._calc_repository.get_prediction("today")["items"]
            self.assertEqual([x["ts_code"] for x in pinned], ["002724.SZ"])
            return [dict(pinned[0], changeSource="realtime", change="+1.00%", quoteAvailable=True, calculationQuoteAvailable=True)]

        with patch.object(app, "_realtime_ready", return_value=True), patch.object(
                type(self.service), "_refresh_existing_prediction_scope", refresh):
            status, result = self.call("/api/v2/realtime/refresh", {"read_token": token}, "POST")
        self.assertEqual(status, 200)
        s2 = result["data"]
        self.assertEqual(s2["result_state"], "fresh")
        for key in ("dataset_id", "pool_hash", "rule_version", "time_context_id"):
            self.assertEqual(s2[key], context[key])
        self.assertNotEqual(s2["read_token"]["value"], token)
        _, first = self.call("/api/v2/predictions", {"read_token": token, "role": "current"})
        self.assertNotIn("change", first["data"]["items"][0])
        _, second = self.call("/api/v2/predictions", {"read_token": s2["read_token"]["value"], "role": "current"})
        self.assertEqual(second["data"]["items"], s2["items"])

    def test_latest_stock_price_refreshes_at_lunch_after_close_weekend_and_preopen(self):
        for iteration, moment in enumerate(("2026-10-30T12:05:00", "2026-10-30T16:00:00", "2026-11-01T12:00:00", "2026-11-02T08:30:00", "2026-11-02T09:10:00")):
            with self.subTest(moment=moment):
                # Shared jobs intentionally merge calls within three seconds.
                # These cases represent separate sessions, not repeated taps.
                self.store.clock = lambda iteration=iteration: time.time() + iteration * 10
                self.service.set_clock(lambda moment=moment: datetime.fromisoformat(moment))
                requested = []
                quotes = {code: {"current": price, "preClose": price, "pctChg": 0,
                                  "source": "腾讯行情", "updatedAt": "2026-10-30T11:30:00"}
                          for code, price in (("600825.SH", 12.34), ("000002.SH", 100))}
                self.service._fetch_realtime_many = lambda codes: (requested.extend(codes) or quotes)
                context = self.context("stock", "600825.SH", entry=moment)
                token = context["read_token"]["value"]
                status, result = self.call("/api/v2/realtime/refresh", {
                    "page": "stock", "security_id": "600825.SH", "read_token": token}, "POST")
                self.assertEqual(status, 200)
                self.assertIn("600825.SH", requested)
                self.assertEqual(result["data"]["currentPrice"], "12.34")
                self.assertEqual(result["data"]["dataQuality"]["quoteUpdatedAt"], "2026-10-30T11:30:00")
                self.assertEqual(result["data"]["refreshStatus"], "applied")
                self.assertEqual(result["data"]["result_state"], "fresh")
                self.assertEqual(result["data"]["calculationInput"]["stock"][-1]["date"], "20261030")

    def test_calendar_failure_does_not_disable_latest_quote_retrieval(self):
        with patch.object(self.service, "_prediction_state", side_effect=RuntimeError("calendar unavailable")):
            self.assertTrue(self.service._detail_realtime_allowed())

    def test_next_prediction_refreshes_prices_without_recomputing_future_thresholds(self):
        self.body["targetTradeDate"] = "20261102"
        self.body["items"][0].update(triggerValue="+8.12%", deviationLabel="10日 +90.00%", change="+0.00%", triggered=False)
        self.service._fetch_realtime_many = lambda codes: {
            code: {"current": 12.34, "preClose": 12, "pctChg": 2.83,
                   "source": "腾讯行情", "updatedAt": "2026-10-30T15:00:00"} for code in codes}
        context = self.context(role="next")
        token = context["read_token"]["value"]
        status, result = self.call("/api/v2/realtime/refresh", {"scope": "next_day", "read_token": token}, "POST")
        self.assertEqual(status, 200)
        data = result["data"]
        self.assertEqual(data["result_state"], "fresh")
        self.assertEqual(data["quoteUpdatedAt"], "2026-10-30T15:00:00")
        self.assertEqual(data["targetTradeDate"], "20261102")
        self.assertEqual(data["items"][0]["currentPrice"], "12.34")
        for field in ("triggerValue", "deviationLabel", "triggered"):
            self.assertEqual(data["items"][0][field], self.body["items"][0][field])

    def test_current_prediction_fetches_at_lunch_and_preserves_pool_on_partial_or_failed_quotes(self):
        self.service.set_clock(lambda: datetime(2026, 10, 30, 12, 5))
        requested = []
        quote = {"current": 12.34, "preClose": 12, "pctChg": 2.83,
                 "source": "腾讯行情", "updatedAt": "2026-10-30T11:30:00"}
        self.service._fetch_realtime_many = lambda codes: (requested.extend(codes) or {code: quote for code in codes})
        context = self.context()
        token = context["read_token"]["value"]
        _, response = self.call("/api/v2/realtime/refresh", {"scope": "today", "read_token": token}, "POST")
        self.assertIn("002724.SZ", requested)
        self.assertEqual(response["data"]["result_state"], "fresh")
        self.assertEqual(response["data"]["quoteUpdatedAt"], quote["updatedAt"])
        self.assertEqual([x["ts_code"] for x in response["data"]["items"]], ["002724.SZ"])
        for iteration, quotes in enumerate(({"002724.SZ": quote}, {}), start=1):
            with self.subTest(quotes=bool(quotes)):
                self.store.clock = lambda iteration=iteration: time.time() + iteration * 10
                self.service._fetch_realtime_many = lambda codes, quotes=quotes: quotes
                _, response = self.call("/api/v2/realtime/refresh", {"scope": "today", "read_token": token}, "POST")
                data = response["data"]
                self.assertEqual(data["result_state"], "stale")
                self.assertEqual(data["reasons"], ["INDEX_QUOTE_UNAVAILABLE" if quotes else "REALTIME_SOURCE_UNAVAILABLE"])
                self.assertEqual([x["ts_code"] for x in data["items"]], ["002724.SZ"])
                if not quotes:
                    self.assertEqual(data["items"], self.body["items"])

    def test_redis_or_source_policy_failure_blocks_off_hours_quotes_too(self):
        for role in ("current", "next"):
            context = self.context(entry=role, role=role)
            with patch.object(app, "_realtime_ready", return_value=False):
                status, response = self.call("/api/v2/realtime/refresh", {
                    "scope": "today" if role == "current" else "next_day", "read_token": context["read_token"]["value"]}, "POST")
            self.assertEqual(status, 503)
            self.assertEqual(response["data"]["reasons"], ["SOURCE_POLICY_OR_REDIS_NOT_READY"])

    def test_stock_price_updates_without_index_and_failed_quotes_preserve_s1(self):
        self.service._fetch_realtime_many = lambda codes: {"600825.SH": {
            "current": 12.34, "preClose": 12, "pctChg": 2.83,
            "source": "腾讯行情", "updatedAt": "2026-10-30T15:00:00"}}
        context = self.context("stock", "600825.SH")
        token = context["read_token"]["value"]
        _, before = self.call("/api/v2/stocks/detail", {"read_token": token, "security_id": "600825.SH"})
        _, response = self.call("/api/v2/realtime/refresh", {
            "page": "stock", "security_id": "600825.SH", "read_token": token}, "POST")
        data = response["data"]
        self.assertEqual(data["currentPrice"], "12.34")
        self.assertEqual(data["result_state"], "stale")
        self.assertEqual(data["reasons"], ["INDEX_QUOTE_UNAVAILABLE"])
        self.assertEqual(data["calculationInput"], before["data"]["calculationInput"])
        last_view_token = data["read_token"]["value"]

        self.store.clock = lambda: time.time() + 10
        self.service._fetch_realtime_many = lambda codes: {}
        _, response = self.call("/api/v2/realtime/refresh", {
            "page": "stock", "security_id": "600825.SH", "read_token": token}, "POST")
        self.assertEqual(response["data"]["currentPrice"], before["data"]["currentPrice"])
        self.assertEqual(response["data"]["reasons"], ["REALTIME_SOURCE_UNAVAILABLE"])
        self.assertEqual(response["data"]["refreshStatus"], "fallback")
        _, response = self.call("/api/v2/realtime/refresh", {
            "page": "stock", "security_id": "600825.SH", "read_token": last_view_token}, "POST")
        self.assertEqual(response["data"]["currentPrice"], "12.34")
        self.assertEqual(response["data"]["reasons"], ["REALTIME_SOURCE_UNAVAILABLE"])

    def test_invalid_undated_old_and_future_quotes_cannot_replace_prices(self):
        self.service.set_clock(lambda: datetime(2026, 11, 1, 12))
        for quote in ({"current": 0, "updatedAt": "2026-10-30T15:00:00"},
                      {"current": float("nan"), "updatedAt": "2026-10-30T15:00:00"},
                      {"current": 12.34},
                      {"current": 12.34, "updatedAt": "2026-10-29T15:00:00"},
                      {"current": 12.34, "updatedAt": "2026-11-01T12:01:00"}):
            with self.subTest(quote=quote):
                self.assertFalse(self.service._usable_latest_quote(quote, "20261030"))

    def test_queued_job_is_authorized_and_returns_failed_stale_s1_on_source_failure(self):
        first = self.context()
        # A different dataset, even with the same role, cannot poll this job.
        self.body["items"][0]["triggered"] = False
        second = self.context(entry="entry-b")
        release = threading.Event()

        def failing_refresh(_context):
            if not release.wait(3):
                raise RuntimeError("test release timed out")
            raise RuntimeError("source unavailable")

        request_job = self.jobs.request
        with patch.object(self.reads, "refresh", side_effect=failing_refresh), patch.object(
                self.jobs, "request", side_effect=lambda context, job_id=None, **_kwargs:
                    request_job(context, job_id, wait_seconds=0)):
            self.addCleanup(release.set)
            status, result = self.call("/api/v2/realtime/refresh", {"read_token": first["read_token"]["value"]}, "POST")
            self.assertEqual(status, 202)
            queued = result["data"]
            self.assertEqual((queued["refresh_state"], queued["result_state"]), ("queued", "stale"))
            path = "/api/v2/jobs/" + queued["job_id"]
            self.assertEqual(self.call(path, {"read_token": second["read_token"]["value"]})[0], 409)
            release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                status, result = self.call(path, {"read_token": first["read_token"]["value"]})
                if status != 202:
                    break
                threading.Event().wait(0.01)
        self.assertEqual(status, 200)
        terminal = result["data"]
        self.assertEqual((terminal["refresh_state"], terminal["result_state"]), ("failed", "stale"))
        self.assertEqual(terminal["reasons"], ["REALTIME_REFRESH_FAILED"])
        self.assertTrue(terminal["items"][0]["triggered"])
        self.assertEqual(terminal["dataset_id"], first["dataset_id"])

    def test_realtime_pool_expansion_is_rejected_and_preserves_s1(self):
        first = self.context()
        self.service._detail_realtime_allowed = lambda: True
        with patch.object(app, "_realtime_ready", return_value=True), patch.object(
                type(self.service), "_refresh_existing_prediction_scope", return_value=[
                    {"ts_code": "600825.SH", "ruleKey": "severe_30d", "changeSource": "realtime"}]):
            status, result = self.call("/api/v2/realtime/refresh", {"read_token": first["read_token"]["value"]}, "POST")
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["refresh_state"], "failed")
        self.assertEqual([x["ts_code"] for x in result["data"]["items"]], ["002724.SZ"])
