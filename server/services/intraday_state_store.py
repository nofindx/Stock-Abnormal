"""Redis-backed temporary state for intraday prediction refreshes."""

from __future__ import annotations

import json
import os
import time
from threading import Lock
from typing import Any, Dict, Optional


class IntradayStateStore:
    """Shared short-lived state with a deterministic local fallback for tests."""

    TTL_SECONDS = 120
    MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
    _local_values: Dict[str, str] = {}
    _local_expiry: Dict[str, float] = {}
    _local_lock = Lock()

    def __init__(self, redis_url: Optional[str] = None, redis_client: Any = None) -> None:
        self.redis_url = str(redis_url or os.getenv("REDIS_URL") or "").strip()
        self._redis = redis_client
        if self._redis is None and self.redis_url:
            try:
                import redis  # type: ignore

                self._redis = redis.Redis.from_url(self.redis_url, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None

    @property
    def shared(self) -> bool:
        return self._redis is not None

    @staticmethod
    def _key(scope: str, target_trade_date: str) -> str:
        return f"stock-abnormal:prediction:intraday:{scope}:{target_trade_date}"

    def get(self, scope: str, target_trade_date: str) -> Optional[Dict[str, Any]]:
        key = self._key(scope, target_trade_date)
        if self._redis:
            raw = self._redis.get(key)
        else:
            with self._local_lock:
                if self._local_expiry.get(key, 0) <= time.monotonic():
                    self._local_values.pop(key, None)
                    self._local_expiry.pop(key, None)
                raw = self._local_values.get(key)
        if not raw:
            return None
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def set(self, scope: str, target_trade_date: str, value: Dict[str, Any]) -> bool:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
            raise ValueError("盘中预测状态超过 2MB 限制")
        key = self._key(scope, target_trade_date)
        if self._redis:
            self._redis.setex(key, self.TTL_SECONDS, raw)
        else:
            with self._local_lock:
                self._local_values[key] = raw
                self._local_expiry[key] = time.monotonic() + self.TTL_SECONDS
        return True

    def delete(self, scope: str, target_trade_date: str) -> None:
        key = self._key(scope, target_trade_date)
        if self._redis:
            self._redis.delete(key)
        else:
            with self._local_lock:
                self._local_values.pop(key, None)
                self._local_expiry.pop(key, None)
