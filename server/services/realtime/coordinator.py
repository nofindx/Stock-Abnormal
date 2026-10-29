"""Redis-backed realtime coordination with a deterministic local test backend.

Redis is a coordination/cache layer only. MySQL remains authoritative for
published views and regulatory data.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, Iterator, Optional
from urllib.parse import quote


def build_realtime_key(kind: str, *parts: str) -> str:
    if kind not in {"quote", "singleflight", "view"}:
        raise ValueError("未知 realtime key 类型")
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"sa:realtime:v1:{kind}:{digest}"


def quote_bucket(now: Optional[float] = None, bucket_seconds: int = 3) -> str:
    """Return a shared UTC epoch bucket for short-lived quote coordination."""

    if bucket_seconds <= 0:
        raise ValueError("quote bucket 必须大于 0 秒")
    timestamp = time.time() if now is None else float(now)
    return str(int(timestamp // bucket_seconds))


@dataclass(frozen=True)
class RealtimeLease:
    key: str
    owner_token: str
    fence: int
    expires_at: float
    takeover: bool = False


class _MemoryBackend:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.values: Dict[str, tuple[float, Any]] = {}
        self.leases: Dict[str, RealtimeLease] = {}
        self.fences: Dict[str, int] = {}
        self.attempts: Dict[str, tuple[float, int]] = {}

    def purge(self) -> None:
        now = time.time()
        for key, (expires, _value) in list(self.values.items()):
            if expires and expires <= now:
                self.values.pop(key, None)
        for key, lease in list(self.leases.items()):
            if lease.expires_at <= now:
                self.leases.pop(key, None)
        for key, (expires, _count) in list(self.attempts.items()):
            if expires <= now:
                self.attempts.pop(key, None)


class RealtimeCoordinator:
    def __init__(self, redis_url: Optional[str] = None, *, backend: Any = None, lease_seconds: int = 10) -> None:
        self.redis_url = redis_url or os.getenv("REDIS_URL", "") or self._redis_url_from_parts()
        self.lease_seconds = int(lease_seconds)
        self._memory = backend or _MemoryBackend()
        self._redis = None
        if self.redis_url:
            try:
                import redis  # type: ignore
                self._redis = redis.Redis.from_url(self.redis_url, decode_responses=True, socket_timeout=2)
            except Exception:
                self._redis = None

    @staticmethod
    def _redis_url_from_parts() -> str:
        """Accept the Tencent Cloud split-variable convention without logging secrets."""

        host = str(os.getenv("REDIS_HOST", "")).strip()
        if not host:
            return ""
        port = str(os.getenv("REDIS_PORT", "6379")).strip() or "6379"
        db = str(os.getenv("REDIS_DB", "0")).strip() or "0"
        password = os.getenv("REDIS_PASSWORD", "")
        scheme = "rediss" if str(os.getenv("REDIS_SSL", "")).lower() in {"1", "true", "yes"} else "redis"
        auth = f":{quote(password, safe='')}@" if password else ""
        return f"{scheme}://{auth}{host}:{port}/{db}"

    @property
    def redis_enabled(self) -> bool:
        return self._redis is not None

    def ping(self) -> bool:
        if not self._redis:
            return False
        try:
            return bool(self._redis.ping())
        except Exception:
            return False

    @contextmanager
    def lease(self, singleflight_key: str) -> Iterator[RealtimeLease]:
        token = uuid.uuid4().hex
        key = f"{singleflight_key}:lease"
        if self._redis:
            if not self._redis.set(key, token, nx=True, ex=self.lease_seconds):
                raise TimeoutError("realtime lease busy")
            attempts_key = f"{key}:attempts"
            attempt = int(self._redis.incr(attempts_key))
            self._redis.expire(attempts_key, self.lease_seconds * 2)
            if attempt > 2:
                # A third owner must not call the upstream source. The lease
                # is released immediately so callers can return queued.
                self._redis.delete(key)
                raise TimeoutError("realtime upstream attempt cap reached")
            fence = int(self._redis.incr(f"{key}:fence"))
            lease = RealtimeLease(singleflight_key, token, fence, time.time() + self.lease_seconds, attempt == 2)
        else:
            with self._memory.lock:
                self._memory.purge()
                previous = self._memory.leases.get(key)
                if previous and previous.expires_at > time.time():
                    raise TimeoutError("realtime lease busy")
                previous_attempts = self._memory.attempts.get(key)
                attempt = (previous_attempts[1] + 1) if previous_attempts else 1
                if attempt > 2:
                    raise TimeoutError("realtime upstream attempt cap reached")
                self._memory.attempts[key] = (time.time() + self.lease_seconds * 2, attempt)
                fence = self._memory.fences.get(key, 0) + 1
                self._memory.fences[key] = fence
                lease = RealtimeLease(singleflight_key, token, fence, time.time() + self.lease_seconds, attempt == 2)
                self._memory.leases[key] = lease
        try:
            yield lease
        finally:
            self.release(lease)

    def release(self, lease: RealtimeLease) -> bool:
        key = f"{lease.key}:lease"
        if self._redis:
            if self._redis.get(key) != lease.owner_token:
                return False
            deleted = bool(self._redis.delete(key))
            # A normally completed request starts a fresh singleflight
            # window; attempts are retained only when the owner disappears
            # and another worker must take over an expired lease.
            self._redis.delete(f"{key}:attempts")
            return deleted
        with self._memory.lock:
            current = self._memory.leases.get(key)
            if not current or current.owner_token != lease.owner_token:
                return False
            self._memory.leases.pop(key, None)
            self._memory.attempts.pop(key, None)
            return True

    def get_or_set(self, key: str, value: Any = None, ttl_seconds: int = 3) -> Any:
        if self._redis:
            raw = self._redis.get(key)
            if raw is not None:
                return json.loads(raw)
            if value is not None:
                self._redis.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds)
            return value
        with self._memory.lock:
            self._memory.purge()
            cached = self._memory.values.get(key)
            if cached is not None:
                return cached[1]
            if value is not None:
                self._memory.values[key] = (time.time() + ttl_seconds, value)
            return value
