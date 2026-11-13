"""Bounded S2 work with durable, authorized cross-instance polling."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import BoundedSemaphore
from server.data.publication_store import PublicationUnavailable


class ReadRefreshJobs:
    def __init__(self, reads, store):
        self.reads, self.store = reads, store
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="read-refresh")
        self.slots = BoundedSemaphore(4)

    def _run(self, context, job):
        try:
            try:
                _data, view = self.reads.refresh(context)
            except Exception:
                payload = deepcopy(self.reads.get(context))
                payload["data"].update(refresh_state="failed", result_state="stale", refreshStatus="fallback",
                                       reasons=["REALTIME_REFRESH_FAILED"])
                view = self.store.freeze_read(payload, payload["scope"])
            self.store.complete_refresh_job(job["job_id"], job["owner"], view)
        finally:
            self.slots.release()

    def request(self, context, job_id=None, wait_seconds=0):
        # Acquire a bounded local slot before claiming shared work, so overload
        # does not leave a claimed job waiting in an unbounded executor queue.
        slot = self.slots.acquire(blocking=False)
        try:
            job = self.store.refresh_job(context, job_id, claim=slot)
        except Exception:
            if slot:
                self.slots.release()
            raise
        if job["owner"]:
            try:
                future = self.executor.submit(self._run, context, job)
            except Exception:
                self.slots.release()
                raise
            if wait_seconds:
                try:
                    future.result(timeout=wait_seconds)
                except TimeoutError:
                    pass
                return self.store.refresh_job(context, job["job_id"])
        elif slot:
            self.slots.release()
        return job

    def close(self):
        self.executor.shutdown(wait=True)
