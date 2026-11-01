import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from server.data.publication_store import PublicationStore, PublicationUnavailable
from server.services.context_service import ContextService, ContextExpired, IdempotencyConflict
from server.services.read_publications import ReadPublications
from server.services.read_refresh_jobs import ReadRefreshJobs


class SharedPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1000.0]
        path = str(Path(self.temp.name) / "shared.db")
        self.a = PublicationStore(sqlite_path=path, clock=lambda: self.now[0])
        self.b = PublicationStore(sqlite_path=path, clock=lambda: self.now[0])

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, identity):
        return {"snapshotId": identity, "items": [], "unresolvedFacts": []}

    def test_second_instance_reads_same_snapshot_and_deduplicated_facts(self):
        fact = {"sourceId": "one", "symbol": "002724", "announcementDate": "20261022", "eventDirection": "up"}
        self.a.publish(self.snapshot("20261101010000"), [fact, fact], "one")
        self.assertEqual(self.b.active_snapshot()["snapshotId"], "20261101010000")
        self.assertEqual(len(self.b.rebuild_facts("002724")), 1)
        self.assertEqual(self.b.health()["outboxPending"], 1)
        self.a.publish(self.snapshot("20261031010000"), [dict(fact, eventDirection="down")], "old")
        self.assertEqual(self.b.rebuild_facts()[0]["eventDirection"], "up")

    def test_failed_publication_rolls_back_facts_pointer_and_outbox(self):
        good = self.snapshot("1")
        self.a.publish(good, [], "one")
        with self.assertRaises(PublicationUnavailable):
            self.a.publish(dict(good, items=[{"symbol": "bad"}]),
                           [{"sourceId": "bad", "symbol": "bad"}], "two")
        self.assertEqual(self.b.active_snapshot(), good)
        self.assertEqual(self.b.rebuild_facts(), [])
        self.assertEqual(self.b.health()["outboxPending"], 1)

    def test_outbox_retry_survives_worker_restart(self):
        self.a.publish(self.snapshot("1"), [], "one")
        self.assertFalse(self.a.dispatch(lambda: False))
        self.assertEqual(self.b.health()["outboxPending"], 1)
        self.assertTrue(self.b.dispatch(lambda: True))
        self.assertEqual(self.a.health()["outboxPending"], 0)
        self.assertFalse(self.a.dispatch(lambda: self.fail("already delivered")))

    def test_immutable_read_survives_pointer_switch_and_expires_closed(self):
        old = self.a.freeze_read({"items": ["A"]}, {"page": "prediction"})
        new = self.b.freeze_read({"items": ["B"]}, {"page": "prediction"})
        self.assertNotEqual(old, new)
        self.assertEqual(self.b.get(old)["items"], ["A"])
        self.now[0] = 1601
        with self.assertRaises(PublicationUnavailable):
            self.b.get(old)

    def test_context_idempotency_is_shared_across_instances(self):
        kwargs = dict(dataset_id="one", dataset_version="one", pool_version="p", pool_hash="p",
            pool_count=1, rule_version="v69", time_context_id="t", target_date="20261030",
            role="current", query={"page": "prediction"}, next_switch_at=2000, stale_until=2000)
        a = ContextService("a" * 32, clock=lambda: self.now[0], store=self.a)
        b = ContextService("a" * 32, clock=lambda: self.now[0], store=self.b)
        c1, _ = a.build_context(**kwargs)
        c2, _ = a.build_context(**dict(kwargs, dataset_id="two"))
        one, _ = a.idempotent_context("entry", {"page": "prediction"}, c1)
        two, _ = b.idempotent_context("entry", {"page": "prediction"}, c2)
        self.assertEqual(one, two)
        with self.assertRaises(IdempotencyConflict):
            b.idempotent_context("entry", {"page": "stock"}, c2)

    def test_read_publication_uses_saved_body_not_mutable_latest(self):
        saved = {"scope": {"page": "prediction", "role": "current", "security_id": ""},
                 "data": {"items": [{"ts_code": "A"}]}, "basis": {}}
        identity = self.a.freeze_read(saved, saved["scope"])
        reads = ReadPublications(object(), self.b)
        context = SimpleNamespace(dataset_id=identity, realtime_view_id="", role="current")
        saved["data"]["items"] = [{"ts_code": "B"}]
        self.assertEqual(reads.get(context)["data"]["items"][0]["ts_code"], "A")

    def test_confirmed_fact_is_not_erased_by_failed_reread(self):
        fact = {"sourceId": "one", "symbol": "002724", "eventDirection": "up"}
        self.a.publish(self.snapshot("1"), [fact], "one")
        snap = dict(self.snapshot("2"), unresolvedFacts=[dict(fact, reason="source_failed")])
        self.b.publish(snap, [], "two")
        self.assertEqual(len(self.a.rebuild_facts()), 1)
        self.assertEqual(self.a.pending(), [])

    def job_context(self, **overrides):
        return SimpleNamespace(**dict(dict(dataset_id="dataset-a", query_hash="scope-a", expires_at=2000), **overrides))

    def test_refresh_job_is_shared_authorized_and_late_owner_is_fenced(self):
        context = self.job_context()
        job = self.a.refresh_job(context, claim=True)
        other = self.b.refresh_job(context, job["job_id"], claim=True)
        self.assertFalse(other["owner"])
        self.now[0] += 31
        successor = self.b.refresh_job(context, job["job_id"], claim=True)
        self.assertTrue(successor["owner"])
        self.assertFalse(self.a.complete_refresh_job(job["job_id"], job["owner"], "late"))
        self.assertTrue(self.b.complete_refresh_job(job["job_id"], successor["owner"], "good"))
        self.assertEqual(self.a.refresh_job(context, job["job_id"])["view_id"], "good")
        with self.assertRaises(PublicationUnavailable):
            self.b.refresh_job(self.job_context(query_hash="other-stock"), job["job_id"])

    def test_refresh_failure_retains_s1_as_stale_terminal_result(self):
        scope = {"page": "prediction", "role": "current", "security_id": ""}
        saved = {"scope": scope, "data": {"items": [{"ts_code": "A"}]}, "basis": {}}
        identity = self.a.freeze_read(saved, scope)
        context = self.job_context(dataset_id=identity)
        reads = SimpleNamespace(get=lambda _context: saved,
            refresh=lambda _context: (_ for _ in ()).throw(RuntimeError("upstream")))
        jobs = ReadRefreshJobs(reads, self.a)
        try:
            result = jobs.request(context, wait_seconds=1)
            payload = self.b.get(result["view_id"], "read")["data"]
            self.assertEqual(payload["items"], [{"ts_code": "A"}])
            self.assertEqual(payload["refresh_state"], "failed")
            self.assertEqual(payload["result_state"], "stale")
        finally:
            jobs.close()

    def test_overload_creates_retryable_queued_job_without_claiming(self):
        job = self.a.refresh_job(self.job_context(), claim=False)
        self.assertEqual(job["status"], "queued")
        self.assertFalse(job["owner"])
        self.assertTrue(self.b.refresh_job(self.job_context(), job["job_id"], claim=True)["owner"])
