#!/usr/bin/env python3
"""检查生产健康状态并输出结构化告警结果。

脚本只读，不向外部系统发送消息。退出码：0 无告警，1 有告警，2 无法读取健康接口。
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_health(base_url: str) -> dict:
    request = Request(base_url.rstrip("/") + "/health", headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise RuntimeError("健康接口返回失败")
    return payload.get("data") or {}


def collect_alerts(data: dict, require_shared_redis: bool = False) -> list[dict]:
    alerts: list[dict] = []
    market = data.get("marketCalc") or {}
    if market.get("lastErrorActive"):
        alerts.append({"severity": "critical", "key": "market_calc", "message": market.get("lastError") or "基础行情任务失败"})
    coverage = market.get("stockBasicCoverage") or {}
    if coverage and coverage.get("complete") is False:
        alerts.append({"severity": "critical", "key": "stock_basic_coverage", "message": "活跃股票基础资料字段不完整"})
    migration = market.get("predictionMigration") or {}
    if migration.get("compatReadEnabled") and int(migration.get("predictionCacheRows") or 0) > 0:
        alerts.append({"severity": "warning", "key": "prediction_cache_migration", "message": "旧预测表仍有记录，迁移兼容读取尚未关闭"})
    for scope in ("today", "next_day"):
        state = (data.get("prediction") or {}).get(scope) or {}
        if state.get("status") in {"failed", "missing", "incomplete"} or state.get("stale"):
            alerts.append({
                "severity": "critical" if state.get("status") == "failed" else "warning",
                "key": f"prediction_{scope}",
                "message": state.get("lastError") or f"{scope} 数据集不可用或已过期",
            })
        if state.get("datasetQuoteComplete") is False and state.get("available"):
            alerts.append({"severity": "critical", "key": f"prediction_{scope}_quote", "message": f"{scope} 行情数据未完整"})
    intraday = data.get("intradayState") or {}
    if require_shared_redis and not (intraday.get("backend") == "redis" and intraday.get("shared") is True):
        alerts.append({"severity": "critical", "key": "shared_redis", "message": "盘中状态未使用共享 Redis"})
    return alerts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--require-shared-redis", action="store_true")
    args = parser.parse_args()
    try:
        data = request_health(args.base_url)
        alerts = collect_alerts(data, args.require_shared_redis)
        result = {"service": "stock-abnormal", "alerts": alerts, "alertCount": len(alerts)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if alerts else 0
    except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"service": "stock-abnormal", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
