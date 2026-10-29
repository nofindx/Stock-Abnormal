"""Dependency-free boundary contracts for the next major release."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re


class TradeState(str, Enum):
    NORMAL = "normal"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"
    UNKNOWN = "unknown"


class Availability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class MetricStatus(str, Enum):
    TRIGGERED = "triggered"
    NOT_TRIGGERED = "not_triggered"
    MAY_TRIGGER = "may_trigger"
    UNAVAILABLE = "unavailable"


class DataReason(str, Enum):
    QUOTE_MISSING = "QUOTE_MISSING"
    QUOTE_STALE = "QUOTE_STALE"
    SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
    ANNOUNCEMENT_BATCH_INCOMPLETE = "ANNOUNCEMENT_BATCH_INCOMPLETE"
    BODY_UNREADABLE = "BODY_UNREADABLE"
    FACT_DIRECTION_UNRESOLVED = "FACT_DIRECTION_UNRESOLVED"
    SOURCE_POLICY_UNFROZEN = "SOURCE_POLICY_UNFROZEN"


@dataclass(frozen=True)
class SourcePolicy:
    """Versioned source/freshness policy shared by all realtime instances."""

    version: str
    content_hash: str
    market_phase: str
    source_name: str = ""
    freshness_seconds: int = 5
    stale_seconds: int = 30
    max_quote_skew_seconds: int = 5

    def validate(self) -> None:
        if not self.version or not self.content_hash or not self.market_phase:
            raise ValueError("source_policy 未完整冻结")
        if self.freshness_seconds < 0 or self.stale_seconds < self.freshness_seconds:
            raise ValueError("source_policy freshness 边界无效")
        if self.max_quote_skew_seconds < 0:
            raise ValueError("source_policy skew 边界无效")


def validate_trade_date(value: str) -> str:
    value = str(value or "")
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("交易日期必须使用 YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def source_policy_from_dict(payload: dict) -> SourcePolicy:
    policy = SourcePolicy(
        version=str(payload.get("version") or ""),
        content_hash=str(payload.get("content_hash") or payload.get("contentHash") or ""),
        market_phase=str(payload.get("market_phase") or payload.get("marketPhase") or ""),
        freshness_seconds=int(payload.get("freshness_seconds", payload.get("freshnessSeconds", 5))),
        stale_seconds=int(payload.get("stale_seconds", payload.get("staleSeconds", 30))),
        max_quote_skew_seconds=int(payload.get("max_quote_skew_seconds", payload.get("maxQuoteSkewSeconds", 5))),
    )
    policy.validate()
    return policy
