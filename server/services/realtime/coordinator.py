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
    # Same policy/bucket pool shares a Redis Cluster slot for MGET. Lease
    # suffixes inherit the tag, so each two-key Lua script is slot-safe too.
    group = hashlib.sha256("|".join(str(part) for part in parts[1:]).encode("utf-8")).hexdigest()[:16]
    return f"sa:realtime:v1:{kind}:{{{group}}}:{digest}"


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
        self.fence_expiry: Dict[str, float] = {}
        self.attempts: Dict[str, tuple[float, int]] = {}

    def purge(self) -> None:
        now = time.time()
        for key, (expires, _value) in list(self.values.items()):
            if expires and expires <= now:
                self.values.pop(key, None)
        for key, expires in list(self.fence_expiry.items()):
            if expires <= now:
                self.fence_expiry.pop(key, None)
                self.fences.pop(key, None)
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
            self._redis.expire(f"{key}:fence", self.lease_seconds * 2)
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
                self._memory.fence_expiry[key] = time.time() + self.lease_seconds * 2
                lease = RealtimeLease(singleflight_key, token, fence, time.time() + self.lease_seconds, attempt == 2)
                self._memory.leases[key] = lease
        try:
            yield lease
        finally:
            self.release(lease)

    def release(self, lease: RealtimeLease) -> bool:
        key = f"{lease.key}:lease"
        if self._redis:
            # Compare and delete atomically: an expired owner cannot remove
            # a successor's lease or attempt budget.
            return bool(self._redis.eval(
                "if redis.call('get',KEYS[1]) == ARGV[1] then "
                "redis.call('del',KEYS[2]); return redis.call('del',KEYS[1]) end; return 0",
                2, key, f"{key}:attempts", lease.owner_token))
        with self._memory.lock:
            current = self._memory.leases.get(key)
            if not current or current.owner_token != lease.owner_token:
                return False
            self._memory.leases.pop(key, None)
            self._memory.attempts.pop(key, None)
            return True

    def get_many(self, keys):
        """One cache round trip for a stock/index pool; never renew TTL on read."""
        keys = list(keys)
        if not keys:
            return {}
        if self._redis:
            return {key: json.loads(raw) for key, raw in zip(keys, self._redis.mget(keys)) if raw is not None}
        with self._memory.lock:
            self._memory.purge()
            return {key: self._memory.values[key][1] for key in keys if key in self._memory.values}

    @contextmanager
    def quote_leases(self, keys):
        """Claim independent securities in one Redis pipeline, without view fencing.

        Returning only owned keys lets intersecting pools fetch their own
        missing subset. Expired owners cannot release a successor's lease.
        """
        keys = list(dict.fromkeys(keys))
        owner = uuid.uuid4().hex
        acquired = []
        if self._redis:
            script = (
                "if not redis.call('set',KEYS[1],ARGV[1],'NX','EX',ARGV[2]) then return 0 end; "
                "local n=redis.call('incr',KEYS[2]); redis.call('expire',KEYS[2],ARGV[2]*2); "
                "if n>2 then redis.call('del',KEYS[1]); return 0 end; return 1")
            with self._redis.pipeline(transaction=False) as pipe:
                for key in keys:
                    pipe.eval(script, 2, f"{key}:lease", f"{key}:lease:attempts", owner, self.lease_seconds)
                results = pipe.execute()
            acquired = [key for key, claimed in zip(keys, results) if claimed]
        else:
            with self._memory.lock:
                self._memory.purge()
                now = time.time()
                for key in keys:
                    lease_key = f"{key}:lease"
                    if lease_key in self._memory.leases:
                        continue
                    attempt = self._memory.attempts.get(lease_key, (0, 0))[1] + 1
                    if attempt > 2:
                        continue
                    self._memory.attempts[lease_key] = (now + self.lease_seconds * 2, attempt)
                    self._memory.leases[lease_key] = RealtimeLease(
                        key, owner, 0, now + self.lease_seconds, attempt == 2)
                    acquired.append(key)
        try:
            yield set(acquired)
        finally:
            if self._redis:
                script = ("if redis.call('get',KEYS[1]) == ARGV[1] then "
                          "redis.call('del',KEYS[2]); return redis.call('del',KEYS[1]) end; return 0")
                with self._redis.pipeline(transaction=False) as pipe:
                    for key in acquired:
                        pipe.eval(script, 2, f"{key}:lease", f"{key}:lease:attempts", owner)
                    pipe.execute()
            else:
                with self._memory.lock:
                    for key in acquired:
                        lease_key = f"{key}:lease"
                        current = self._memory.leases.get(lease_key)
                        if current and current.owner_token == owner:
                            self._memory.leases.pop(lease_key, None)
                            self._memory.attempts.pop(lease_key, None)

    def put_many(self, values, ttl_seconds=3):
        """Fill missing quote keys in one round trip; hits keep their old expiry."""
        if self._redis:
            with self._redis.pipeline(transaction=False) as pipe:
                for key, value in values.items():
                    pipe.set(key, json.dumps(value, ensure_ascii=False), ex=ttl_seconds, nx=True)
                pipe.execute()
        else:
            with self._memory.lock:
                self._memory.purge()
                for key, value in values.items():
                    if key not in self._memory.values:
                        self._memory.values[key] = (time.time() + ttl_seconds, value)

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
