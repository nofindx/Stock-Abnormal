#!/usr/bin/env python3
"""检查生产健康状态并按需发送告警 webhook。

默认只读并输出 JSON。传入 ``--send`` 且设置 ``ALERT_WEBHOOK_URL`` 后才发送
POST 通知，避免把检查脚本误配置成外发数据源。
退出码：0 无告警，1 有告警，2 无法读取健康接口或发送失败。
"""

from __future__ import annotations

import argparse
import json
import os
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


def send_webhook(url: str, payload: dict) -> None:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "stock-abnormal-health-check"},
    )
    with urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"告警 webhook HTTP {response.status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--require-shared-redis", action="store_true")
    parser.add_argument("--send", action="store_true", help="发送到 ALERT_WEBHOOK_URL")
    args = parser.parse_args()
    try:
        data = request_health(args.base_url)
        alerts = collect_alerts(data, args.require_shared_redis)
        result = {"service": "stock-abnormal", "alerts": alerts, "alertCount": len(alerts)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.send and alerts:
            webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip()
            if not webhook:
                raise RuntimeError("--send 需要设置 ALERT_WEBHOOK_URL")
            send_webhook(webhook, result)
        return 1 if alerts else 0
    except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"service": "stock-abnormal", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
