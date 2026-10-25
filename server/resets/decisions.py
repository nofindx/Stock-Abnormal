"""公告事实的规则重置决策。

只将证据充分的普通/严重异动事实转换成生效交易日边界；不读取行情、不写数据库，
也不执行偏离计算。公告事实的来源状态由公告模块和数据仓储负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from server.rules.catalog import RESET_POLICIES


RESET_AUTHORIZED_SOURCE_TYPES = {"issuer-disclosure", "exchange-disclosure"}
RESET_EVIDENCE_CODE = "official_severe_abnormality_body"
ORDINARY_RESET_EVIDENCE_CODE = "official_ordinary_abnormality_body"
ORDINARY_TITLE_EVIDENCE_CODE = "official_ordinary_abnormality_title_market"
RESET_EVIDENCE_CODES = {
    RESET_EVIDENCE_CODE,
    ORDINARY_RESET_EVIDENCE_CODE,
    ORDINARY_TITLE_EVIDENCE_CODE,
}
OFFICIAL_HOSTS = ("cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn")


def _policy_for_tone(risk_tone: str) -> Optional[Dict[str, Any]]:
    return next(
        (policy for policy in RESET_POLICIES.values() if risk_tone in policy["fact_tones"]),
        None,
    )


def has_auditable_reset_evidence(item: Dict[str, Any]) -> bool:
    evidence = item.get("resetEvidence") or {}
    source_url = str(evidence.get("sourceUrl") or "")
    body_hash = str(evidence.get("bodyHash") or "")
    host = (urlparse(source_url).hostname or "").lower()
    official_host = any(host == domain or host.endswith(f".{domain}") for domain in OFFICIAL_HOSTS)
    return bool(
        item.get("ruleResetConfirmed")
        and evidence.get("code") in RESET_EVIDENCE_CODES
        and evidence.get("sourceType") in RESET_AUTHORIZED_SOURCE_TYPES
        and evidence.get("verifiedRiskTone") == item.get("riskTone")
        and len(body_hash) == 64
        and all(character in "0123456789abcdef" for character in body_hash.lower())
        and official_host
    )


def monitor_reset_date(monitoring: Optional[Dict[str, Any]]) -> str:
    """Return the YYYYMMDD baseline date for an evidence-confirmed event."""
    if not monitoring:
        return ""
    risk_tone = str(monitoring.get("riskTone") or "")
    policy = _policy_for_tone(risk_tone)
    expected_evidence = str((policy or {}).get("reset_evidence_code") or "")
    if not expected_evidence:
        return ""
    evidence = monitoring.get("resetEvidence") or {}
    if (
        not monitoring.get("ruleResetConfirmed")
        or evidence.get("code") != expected_evidence
        or not has_auditable_reset_evidence(monitoring)
    ):
        return ""
    explicit = str(monitoring.get("ruleResetDate") or "").replace("-", "")[:8]
    if explicit:
        return explicit
    dates = [str(value).replace("-", "")[:8] for value in (monitoring.get("monitorDates") or []) if value]
    return dates[1] if len(dates) >= 2 else ""


@dataclass(frozen=True)
class ResetContext:
    ordinary_reset_dates: List[str]
    severe_reset_dates: List[str]
    latest_reset_date: str
    latest_reset_scope: str
    ordinary_events: List[Dict[str, Any]]
    reset_evidence: Optional[Dict[str, Any]]


def build_reset_context(facts: Iterable[Dict[str, Any]]) -> ResetContext:
    """Build the deterministic calculation boundary from confirmed facts."""
    fact_list = list(facts)
    dated = [(item, monitor_reset_date(item)) for item in fact_list]
    ordinary_dates = sorted({
        day for item, day in dated
        if day and (_policy_for_tone(str(item.get("riskTone") or "")) or {}).get("scope") == "ordinary"
    })
    severe_dates = sorted({
        day for item, day in dated
        if day and (_policy_for_tone(str(item.get("riskTone") or "")) or {}).get("scope") == "severe"
    })
    all_dates = sorted(set(ordinary_dates) | set(severe_dates))
    latest_date = all_dates[-1] if all_dates else ""
    latest_facts = [item for item, day in dated if day == latest_date] if latest_date else []
    latest_severe = next(
        (item for item in reversed(latest_facts)
         if (_policy_for_tone(str(item.get("riskTone") or "")) or {}).get("scope") == "severe"),
        latest_facts[-1] if latest_facts else None,
    )
    latest_scope = (
        str((_policy_for_tone(str(latest_severe.get("riskTone") or "")) or {}).get("scope"))
        if latest_severe else ""
    )
    ordinary_events = []
    for item, _day in dated:
        evidence = item.get("resetEvidence") or {}
        if (
            item.get("riskTone") == "ordinary-abnormality"
            and (item.get("countsForSameDirection") or evidence.get("code") == ORDINARY_TITLE_EVIDENCE_CODE)
            and item.get("eventDate")
        ):
            ordinary_events.append({
                "sourceId": item.get("sourceId", ""),
                "startDate": item.get("eventDate", ""),
                "endDate": item.get("eventDate", ""),
                "direction": item.get("eventDirection", ""),
                "riskTone": item.get("riskTone", "ordinary-abnormality"),
                "sourceUrl": item.get("sourceUrl", ""),
                "confirmationStatus": item.get("confirmationStatus", ""),
                "verificationMode": evidence.get("verificationMode", ""),
            })
    return ResetContext(
        ordinary_reset_dates=ordinary_dates,
        severe_reset_dates=severe_dates,
        latest_reset_date=latest_date,
        latest_reset_scope=latest_scope,
        ordinary_events=ordinary_events,
        reset_evidence=(latest_severe or {}).get("resetEvidence") if latest_date else None,
    )
