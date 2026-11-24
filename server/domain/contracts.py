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
    """Versioned source identity. Quote timing uses realtime.validation only."""

    version: str
    content_hash: str
    market_phase: str
    source_name: str = ""

    def validate(self) -> None:
        if not self.version or not self.content_hash or not self.market_phase:
            raise ValueError("source_policy 未完整冻结")


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
        source_name=str(payload.get("source_name") or payload.get("sourceName") or ""),
    )
    policy.validate()
    return policy
