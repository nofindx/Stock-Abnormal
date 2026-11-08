"""HTTP regressions for actual authorized reads, not token labels alone."""
import json
import tempfile
import threading
import time
import unittest
from copy import deepcopy
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
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), app.ApiHandler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.jobs.close()
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

    def context(self, page="prediction", code="", entry="entry-a"):
        status, response = self.call("/api/v2/context", {"page": page, "role": "current", "security_id": code, "entry_id": entry})
        self.assertEqual(status, 200)
        return response["data"]

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
        self.assertEqual(response["data"]["refreshStatus"], "not_allowed")
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
            return [dict(pinned[0], changeSource="realtime", change="+1.00%")]

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
