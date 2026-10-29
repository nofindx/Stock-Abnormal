"""Signed S1 read contexts and short-lived read tokens.

The service is deliberately independent from the legacy API. Production can
replace the in-memory idempotency store with the MySQL ``idempotency_record``
adapter without changing token semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Dict, Mapping, MutableMapping, Optional


class ContextExpired(ValueError):
    code = "context_expired"


class IdempotencyConflict(ValueError):
    code = "idempotency_conflict"


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ReadContext:
    dataset_id: str
    dataset_version: str
    pool_version: str
    pool_hash: str
    pool_count: int
    rule_version: str
    time_context_id: str
    target_date: str
    role: str
    query_hash: str
    realtime_view_id: str = ""
    expires_at: float = 0.0
    stale_until: float = 0.0

    def as_payload(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "pool_version": self.pool_version,
            "pool_hash": self.pool_hash,
            "pool_count": self.pool_count,
            "rule_version": self.rule_version,
            "time_context_id": self.time_context_id,
            "target_date": self.target_date,
            "role": self.role,
            "query_hash": self.query_hash,
            "realtime_view_id": self.realtime_view_id,
            "expires_at": self.expires_at,
            "stale_until": self.stale_until,
        }


class ContextService:
    def __init__(self, secret: Optional[str] = None, *, clock=time.time, token_ttl=300, idempotency_ttl=86400):
        self.secret = (secret or "").encode("utf-8")
        if len(self.secret) < 32:
            raise ValueError("context 签名密钥至少 32 字节")
        self.clock = clock
        self.token_ttl = int(token_ttl)
        self.idempotency_ttl = int(idempotency_ttl)
        self._idempotency: MutableMapping[str, tuple[str, ReadContext, float]] = {}

    @staticmethod
    def query_hash(query: Mapping[str, Any]) -> str:
        return hashlib.sha256(_canonical(dict(query))).hexdigest()

    @classmethod
    def scope_matches(cls, context: ReadContext, *, page: str, role: str, security_id: str = "") -> bool:
        """Check that a valid token is bound to the exact read scope."""

        expected = {"page": page, "role": role, "security_id": security_id}
        return context.role == role and hmac.compare_digest(context.query_hash, cls.query_hash(expected))

    def issue(self, context: ReadContext) -> str:
        now = self.clock()
        payload = context.as_payload()
        if context.expires_at <= now:
            raise ContextExpired("context 已过期")
        raw = base64.urlsafe_b64encode(_canonical(payload)).decode().rstrip("=")
        sig = hmac.new(self.secret, raw.encode(), hashlib.sha256).digest()
        encoded_sig = base64.urlsafe_b64encode(sig).decode().rstrip("=")
        return f"v1.{raw}.{encoded_sig}"

    def verify(self, token: str) -> ReadContext:
        try:
            version, raw, encoded_sig = str(token).split(".", 2)
            if version != "v1":
                raise ValueError
            expected = hmac.new(self.secret, raw.encode(), hashlib.sha256).digest()
            actual = base64.urlsafe_b64decode(encoded_sig + "===")
            if not hmac.compare_digest(expected, actual):
                raise ValueError
            payload = json.loads(base64.urlsafe_b64decode(raw + "===").decode("utf-8"))
            context = ReadContext(**payload)
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
            raise ContextExpired("read_token 无效")
        if self.clock() >= context.expires_at:
            raise ContextExpired("read_token 已过期")
        return context

    def build_context(self, *, dataset_id: str, dataset_version: str, pool_version: str,
                      pool_hash: str, pool_count: int, rule_version: str,
                      time_context_id: str, target_date: str, role: str,
                      query: Mapping[str, Any], next_switch_at: float,
                      stale_until: float, realtime_view_id: str = "") -> tuple[ReadContext, str]:
        now = self.clock()
        expires_at = min(now + self.token_ttl, float(next_switch_at), float(stale_until))
        context = ReadContext(
            dataset_id=str(dataset_id), dataset_version=str(dataset_version),
            pool_version=str(pool_version), pool_hash=str(pool_hash), pool_count=int(pool_count),
            rule_version=str(rule_version), time_context_id=str(time_context_id),
            target_date=str(target_date), role=str(role), query_hash=self.query_hash(query),
            realtime_view_id=str(realtime_view_id), expires_at=expires_at, stale_until=float(stale_until),
        )
        return context, self.issue(context)

    def idempotent_context(self, entry_id: str, request: Mapping[str, Any], context: ReadContext) -> tuple[ReadContext, str]:
        fingerprint = hashlib.sha256(_canonical(dict(request))).hexdigest()
        now = self.clock()
        old = self._idempotency.get(str(entry_id))
        if old and old[2] > now:
            if old[0] != fingerprint:
                raise IdempotencyConflict("entry_id 请求指纹不一致")
            return old[1], self.issue(old[1])
        self._idempotency[str(entry_id)] = (fingerprint, context, now + self.idempotency_ttl)
        return context, self.issue(context)


def random_entry_id() -> str:
    return secrets.token_urlsafe(18)
