#!/usr/bin/env python3
"""Run the prediction API contract checks against a local or deployed service."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(url: str, method: str = "GET", body: object = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=8) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def check_prediction(payload: dict, scope: str) -> None:
    assert payload.get("code") == 0, (scope, payload)
    data = payload.get("data") or {}
    for field in ("targetTradeDate", "quoteUpdatedAt", "items", "dataUnavailable", "datasetQuoteComplete"):
        assert field in data, (scope, field)
    for item in data["items"]:
        assert item.get("ruleKey") in {"ordinary_3d", "same_direction_10d", "severe_10d", "severe_30d"}, item
        for field in ("predictionKey", "ts_code", "deviationLabel", "triggerCondition", "triggerValue", "triggered", "cardTone", "quoteUpdatedAt"):
            assert field in item, (scope, field, item)
        assert item.get("triggerValue", "") and "上涨" not in item.get("triggerValue", ""), item
        assert "已达到阈值" not in str(item), item
        assert "currentPrice" not in item and "price" not in item, item
        assert bool(item.get("triggered")) == (item.get("cardTone") == "triggered"), item


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--require-data", action="store_true", help="上游数据不可用时返回失败；生产验收使用")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    status, health = request_json(f"{base}/health")
    assert status == 200 and health.get("code") == 0, health
    market_calc = (health.get("data") or {}).get("marketCalc") or {}
    for field in ("stockBasicCoverage", "predictionMigration"):
        assert field in market_calc, ("health.marketCalc", field, health)
    status, coverage = request_json(f"{base}/api/market/history-coverage?offset=0&limit=5")
    assert status == 200 and coverage.get("code") == 0, ("history-coverage", status, coverage)
    coverage_data = coverage.get("data") or {}
    for field in ("asOfTradeDate", "requiredReturnCount", "activeStockCount", "completeStockCount", "incompleteStockCount", "categoryCounts", "items", "pagination"):
        assert field in coverage_data, ("history-coverage", field, coverage)
    assert coverage_data["requiredReturnCount"] == 30, coverage_data
    assert coverage_data["activeStockCount"] == coverage_data["completeStockCount"] + coverage_data["incompleteStockCount"], coverage_data
    assert len(coverage_data["items"]) <= 5, coverage_data
    assert all(not ({"close", "price", "daily"} & set(item)) for item in coverage_data["items"]), coverage_data
    for scope in ("today", "next_day"):
        status, payload = request_json(f"{base}/api/predictions?scope={scope}")
        if status in (503, 500) and payload.get("code") in (50301, 50302) and not args.require_data:
            print(f"{scope}: upstream data unavailable, contract data assertions skipped")
            continue
        assert status == 200, (scope, status, payload)
        check_prediction(payload, scope)
    status, invalid = request_json(f"{base}/api/predictions?scope=invalid")
    assert status == 400 and invalid.get("code") == 40001, invalid
    status, malformed = request_json(f"{base}/api/predictions/refresh", "POST", "malformed")
    assert status == 400 and malformed.get("code") == 40001, malformed
    status, refreshed = request_json(f"{base}/api/predictions/refresh", "POST", {"scope": "today"})
    if status in (503, 500) and refreshed.get("code") in (50301, 50302) and not args.require_data:
        print("refresh: upstream data unavailable, refresh assertions skipped")
        print("API contract checks passed (transport-only local mode)")
        return 0
    assert status == 200 and refreshed.get("code") == 0, refreshed
    if (refreshed.get("data") or {}).get("refreshStatus") == "applied":
        assert (refreshed.get("data") or {}).get("dataStage") == "realtime", refreshed
        status, stable = request_json(f"{base}/api/predictions?scope=today")
        assert status == 200 and stable.get("code") == 0, stable
        assert (stable.get("data") or {}).get("dataStage") != "realtime", "GET 不应读取盘中刷新结果"
    quote_status, quote_invalid = request_json(f"{base}/api/quotes/realtime", "POST", {"ts_codes": []})
    assert quote_status == 400 and quote_invalid.get("code") == 40001, quote_invalid
    print("API contract checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, URLError, json.JSONDecodeError) as exc:
        print(f"API contract checks failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
