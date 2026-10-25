"""股票当前状态的数据契约构造。

规则结果、公告事实和数据可用性在数据域合并成可审计的当前状态。此处不改写
四项计算结果；API/页面消费这个统一状态，不再依赖独立的状态投影服务。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable


def build_stock_state(
    calculation: Dict[str, Any] | None,
    facts: Iterable[Dict[str, Any]] = (),
    *,
    data_available: bool = True,
    data_error: str = "",
    market_data_state: str = "complete",
    facts_data_state: str = "available",
) -> Dict[str, Any]:
    fact_list = list(facts)
    tones = {str(item.get("riskTone") or item.get("factType") or "") for item in fact_list}
    warnings = (calculation or {}).get("warnings") or []
    severe_rules = {"10 日同向", "10日同向", "10 日偏离", "10日偏离", "30 日偏离", "30日偏离"}
    severe_triggered = any(
        (
            str(item.get("title") or item.get("ruleKey") or "") in severe_rules
            and (
                bool(item.get("triggered"))
                or bool(item.get("upTriggered"))
                or bool(item.get("downTriggered"))
                or max(int(item.get("up") or 0), int(item.get("down") or 0))
                >= int(item.get("upThreshold") or item.get("threshold") or 10**9)
            )
        )
        for item in warnings
    )
    ordinary_triggered = any(
        str(item.get("title") or item.get("ruleKey") or "") in {"3 日偏离", "3日偏离"}
        and (
            bool(item.get("triggered"))
            or bool(item.get("upTriggered"))
            or bool(item.get("downTriggered"))
        )
        for item in warnings
    )
    if severe_triggered:
        market_risk = "severe-risk"
    elif ordinary_triggered:
        market_risk = "ordinary-risk"
    elif market_data_state == "unavailable":
        market_risk = "unknown"
    elif market_data_state != "complete":
        market_risk = "incomplete"
    else:
        market_risk = "safe"

    if not data_available or "unavailable" in {market_data_state, facts_data_state}:
        data_state = "unavailable"
    elif market_data_state != "complete" or facts_data_state not in {"available", "complete"}:
        data_state = "partial"
    else:
        data_state = "available"

    if "delisting-warning" in tones:
        regulatory_state = "delisting"
    elif any(value in tones for value in ("severe-10d", "severe-30d", "severe-abnormality")):
        regulatory_state = "severe-monitoring"
    elif "ordinary" in tones or "ordinary-abnormality" in tones:
        regulatory_state = "ordinary-warning"
    else:
        regulatory_state = "none"
    trading_state = "suspended" if "suspension" in tones else "normal"

    # 仅为旧消费者/摘要提供一个汇总值，四个独立状态轴始终保留。
    if regulatory_state == "delisting":
        summary_status = "delisting-warning"
    elif trading_state == "suspended":
        summary_status = "suspended"
    elif regulatory_state == "severe-monitoring":
        summary_status = "severe-monitoring"
    elif market_risk == "severe-risk":
        summary_status = "severe-risk"
    elif regulatory_state == "ordinary-warning" or market_risk == "ordinary-risk":
        summary_status = "ordinary-risk"
    elif data_state != "available" or market_risk in {"unknown", "incomplete"}:
        summary_status = "unknown"
    else:
        summary_status = "safe"
    return {
        "status": summary_status,
        "marketRiskState": market_risk,
        "regulatoryState": regulatory_state,
        "tradingState": trading_state,
        "dataState": data_state,
        "marketDataState": market_data_state,
        "factsDataState": facts_data_state,
        "dataAvailable": data_state == "available",
        "dataError": "" if data_state == "available" else (data_error or "数据覆盖不完整"),
        "facts": fact_list,
        "calculation": calculation or {},
    }
