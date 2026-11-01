"""HTTP regressions for actual authorized reads, not token labels alone."""
import json
import tempfile
import threading
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
