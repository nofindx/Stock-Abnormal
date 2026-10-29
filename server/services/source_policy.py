"""Versioned realtime source policy and G26 release check."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from typing import Any, Dict, Iterable, List

from server.domain.contracts import SourcePolicy


def policy_hash(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_source_policy(market_phase: str = "continuous") -> SourcePolicy:
    """Load one evidence-backed immutable policy; missing fields block S2."""

    raw = os.getenv("SOURCE_POLICY_JSON", "")
    if not raw:
        raise ValueError("source_policy 未配置；G26 阻断 S2")
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("SOURCE_POLICY_JSON 无法解析") from exc
    if not isinstance(values, dict):
        raise ValueError("SOURCE_POLICY_JSON 必须是单个来源策略对象")
    if str(values.get("market_phase") or "") != market_phase:
        raise ValueError("source_policy 不适用于当前交易阶段")
    required = ("version", "freshness_seconds", "stale_seconds", "max_quote_skew_seconds", "content_hash")
    if any(key not in values for key in required):
        raise ValueError("source_policy 缺少版本、阈值或内容哈希")
    content_hash = str(values.pop("content_hash"))
    expected_hash = policy_hash(values)
    configured_hash = os.getenv("SOURCE_POLICY_HASH", "")
    if content_hash != expected_hash or configured_hash != content_hash:
        raise ValueError("source_policy 内容哈希与实例配置不一致")
    policy = SourcePolicy(content_hash=content_hash, **values)
    policy.validate()
    return policy


def load_source_policies(market_phase: str = "continuous") -> List[SourcePolicy]:
    """Load every enabled source policy for a phase.

    ``SOURCE_POLICY_JSON`` accepts either the legacy single object or a JSON
    array. Arrays are required when more than one realtime source is enabled;
    each object carries its own source name and content hash.
    """

    raw = os.getenv("SOURCE_POLICY_JSON", "")
    if not raw:
        raise ValueError("source_policy 未配置；G26 阻断 S2")
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("SOURCE_POLICY_JSON 无法解析") from exc
    entries = values if isinstance(values, list) else [values]
    if not entries or not all(isinstance(item, dict) for item in entries):
        raise ValueError("SOURCE_POLICY_JSON 必须是对象或对象数组")
    policies = []
    for entry in entries:
        if str(entry.get("market_phase") or "") != market_phase:
            raise ValueError("source_policy 不适用于当前交易阶段")
        payload = dict(entry)
        content_hash = str(payload.pop("content_hash", ""))
        if not content_hash or policy_hash(payload) != content_hash:
            raise ValueError("source_policy 内容哈希不匹配")
        policy = SourcePolicy(content_hash=content_hash, **payload)
        policy.validate()
        if not policy.source_name:
            raise ValueError("source_policy 缺少 source_name")
        policies.append(policy)
    configured_hash = os.getenv("SOURCE_POLICY_HASH", "")
    aggregate = policy_hash({"policies": [asdict(item) for item in policies]})
    if configured_hash and configured_hash not in {aggregate, *(item.content_hash for item in policies)}:
        raise ValueError("source_policy 集合哈希与实例配置不一致")
    return policies


def source_policy_gate(policies: Iterable[SourcePolicy]) -> Dict[str, Any]:
    checked = list(policies)
    errors = []
    for policy in checked:
        try:
            policy.validate()
        except ValueError as exc:
            errors.append(str(exc))
    return {
        "gateId": "G26",
        "status": "pass" if checked and not errors else "block",
        "count": len(checked),
        "errors": errors,
        "reason": "每个启用来源/交易阶段必须有证据支持的冻结版本和匹配配置哈希",
    }


def policy_payload(policy: SourcePolicy) -> Dict[str, Any]:
    return asdict(policy)
