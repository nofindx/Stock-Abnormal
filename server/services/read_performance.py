"""Bounded process-local stage totals; never keep identifiers or payloads."""
from contextlib import contextmanager
from threading import Lock
import time


class ReadPerformance:
    STAGES = frozenset({"context.capture", "context.authorize", "context.response",
        "s1.monitor", "s1.inputs", "s1.render", "s1.freeze", "s1.prediction", "s2.refresh"})

    def __init__(self):
        self._lock = Lock()
        self._stages = {}

    @contextmanager
    def measure(self, stage):
        if stage not in self.STAGES:
            raise ValueError("unknown performance stage")
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            with self._lock:
                row = self._stages.setdefault(stage, {"count": 0, "totalMs": 0, "lastMs": 0, "maxMs": 0})
                row.update(count=row["count"] + 1, totalMs=row["totalMs"] + elapsed,
                           lastMs=elapsed, maxMs=max(row["maxMs"], elapsed))

    def snapshot(self):
        with self._lock:
            return {stage: {"count": row["count"], "lastMs": round(row["lastMs"], 1),
                "maxMs": round(row["maxMs"], 1), "meanMs": round(row["totalMs"] / row["count"], 1)}
                for stage, row in self._stages.items()}


READ_PERFORMANCE = ReadPerformance()
