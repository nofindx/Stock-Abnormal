"""Tushare 数据源适配器。

密钥只在后端进程中读取，微信小程序只访问本服务的 JSON API。
这里不打印 token，也不把 token 写入响应、缓存或日志。
"""

from __future__ import annotations

import os
import json
import time
from threading import Lock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import tushare as ts


class TushareUnavailable(RuntimeError):
    """数据源不可用或授权失败。"""


def _read_local_token() -> str:
    """开发机兜底读取本地密钥文件，不向调用方暴露内容。

    支持两种本地格式：
    1. `key/key.txt`：纯 Token 文本；
    2. `key/tushareMcp.txt`：Tushare MCP JSON 配置，从 URL 查询参数读取 Token。
    """

    project_root = Path(__file__).resolve().parents[2]
    mcp_config_path = project_root / "key" / "tushareMcp.txt"
    try:
        config = json.loads(mcp_config_path.read_text(encoding="utf-8"))
        url = config["mcpServers"]["tushareMcp"]["url"]
        token = parse_qs(urlparse(url).query).get("token", [""])[0].strip()
        if token:
            return token
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass

    direct_token_path = project_root / "key" / "key.txt"
    try:
        direct_token = direct_token_path.read_text(encoding="utf-8").strip()
        if direct_token and not direct_token.startswith("{"):
            return direct_token
    except OSError:
        pass
    return ""


class TushareClient:
    """封装 Tushare Pro API，并统一处理鉴权、超时和错误信息。"""

    def __init__(self, token: Optional[str] = None) -> None:
        # 优先环境变量，方便部署；本地开发再读取未提交的 key 文件。
        self.token = (token or os.getenv("TUSHARE_TOKEN") or _read_local_token()).strip()
        self._pro = None
        self._cache: Dict[str, tuple[float, Any]] = {}
        self._cache_lock = Lock()
        # 只保留聚合统计，不保存 token、请求参数或上游响应正文。
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._stats_lock = Lock()

    @property
    def available(self) -> bool:
        """是否配置了 token；token 有效性仍需通过请求验证。"""

        return bool(self.token)

    def _api(self):
        if not self.available:
            raise TushareUnavailable("未配置 Tushare Token")
        if self._pro is None:
            self._pro = ts.pro_api(self.token)
        return self._pro

    def call(self, method: str, **kwargs: Any):
        """调用指定接口，统一转换为不含敏感信息的异常。"""
        started = time.monotonic()
        try:
            result = getattr(self._api(), method)(**kwargs)
            self._record_stat(method, True, time.monotonic() - started)
            return result
        except Exception as exc:  # Tushare 异常类型在不同版本不一致。
            self._record_stat(method, False, time.monotonic() - started, exc)
            message = str(exc)
            if "token" in message.lower() or "积分" in message or "权限" in message:
                raise TushareUnavailable("Tushare 授权或接口权限不可用") from exc
            raise TushareUnavailable(f"Tushare {method} 请求失败") from exc

    @staticmethod
    def _stat_time() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _record_stat(self, method: str, success: bool, elapsed: float, error: Optional[Exception] = None) -> None:
        with self._stats_lock:
            item = self._stats.setdefault(method, {
                "requests": 0, "successes": 0, "failures": 0,
                "lastLatencyMs": None, "lastRequestAt": "",
                "lastSuccessAt": "", "lastFailureAt": "", "lastError": "",
            })
            item["requests"] += 1
            item["lastLatencyMs"] = round(elapsed * 1000, 1)
            item["lastRequestAt"] = self._stat_time()
            if success:
                item["successes"] += 1
                item["lastSuccessAt"] = item["lastRequestAt"]
                item["lastError"] = ""
            else:
                item["failures"] += 1
                item["lastFailureAt"] = item["lastRequestAt"]
                # 健康接口只保留类别，避免第三方异常带出请求参数或响应正文。
                message = str(error or "请求失败").lower()
                if "token" in message or "积分" in message or "权限" in message:
                    item["lastError"] = "授权或接口权限不可用"
                else:
                    item["lastError"] = "上游接口调用失败"

    def health_stats(self) -> Dict[str, Any]:
        """返回数据源健康统计；结果不含密钥、参数或行情正文。"""

        with self._stats_lock:
            methods = {name: dict(value) for name, value in self._stats.items()}
        totals = {"requests": 0, "successes": 0, "failures": 0}
        for value in methods.values():
            for key in totals:
                totals[key] += int(value.get(key) or 0)
        totals["successRate"] = round(totals["successes"] / totals["requests"], 4) if totals["requests"] else None
        return {"configured": self.available, "totals": totals, "methods": methods}

    def _cached(self, key: str, ttl: int, loader):
        """进程内短缓存，避免页面切换重复请求同一份日历/行情。"""

        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < ttl:
                value = cached[1]
                return value.copy() if hasattr(value, "copy") else value
            value = loader()
            self._cache[key] = (now, value)
            return value.copy() if hasattr(value, "copy") else value

    def stock_basic(self, force: bool = False):
        """读取全部上市 A 股基础资料。"""

        if force:
            # 每日后台任务需要识别新上市股票，不能被进程内 6 小时缓存挡住。
            return self.call(
                "stock_basic",
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name,market,exchange,list_date",
            )
        return self._cached(
            "stock_basic:L",
            21600,
            lambda: self.call(
                "stock_basic",
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name,market,exchange,list_date",
            ),
        )

    def trade_cal(self, start_date: str, end_date: str):
        """读取交易日历。"""

        return self._cached(
            f"trade_cal:SSE:{start_date}:{end_date}",
            43200,
            lambda: self.call("trade_cal", exchange="SSE", start_date=start_date, end_date=end_date),
        )

    def daily(self, ts_code: Optional[str] = None, trade_date: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """读取日行情；支持单票区间和单交易日全市场。"""

        params: Dict[str, Any] = {}
        if ts_code:
            params["ts_code"] = ts_code
        if trade_date:
            params["trade_date"] = trade_date
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        key = "daily:" + json.dumps(params, ensure_ascii=False, sort_keys=True)
        return self._cached(key, 300 if trade_date else 900, lambda: self.call("daily", **params))

    def index_daily(self, ts_code: Optional[str] = None, trade_date: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """读取指数日行情；指数数据必须使用 index_daily 接口。"""

        params: Dict[str, Any] = {}
        if ts_code:
            params["ts_code"] = ts_code
        if trade_date:
            params["trade_date"] = trade_date
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        key = "index_daily:" + json.dumps(params, ensure_ascii=False, sort_keys=True)
        return self._cached(key, 1800, lambda: self.call("index_daily", **params))

    def daily_basic(self, ts_code: Optional[str] = None, trade_date: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """读取换手率等日频指标。"""

        params: Dict[str, Any] = {"fields": "ts_code,trade_date,turnover_rate,close"}
        if ts_code:
            params["ts_code"] = ts_code
        if trade_date:
            params["trade_date"] = trade_date
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        key = "daily_basic:" + json.dumps(params, ensure_ascii=False, sort_keys=True)
        return self._cached(key, 900, lambda: self.call("daily_basic", **params))

    def latest_trade_date(self) -> str:
        """返回最近一个交易日，优先查历年最近 20 天。"""

        # 云托管容器默认时区可能是 UTC；交易日阶段统一使用上海时间，
        # 避免北京时间 00:00-08:00 被误判成前一自然日。
        try:
            from zoneinfo import ZoneInfo
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        except (ImportError, KeyError):
            today = datetime.now(timezone(timedelta(hours=8))).date()
        frame = self.trade_cal(
            (today - timedelta(days=20)).strftime("%Y%m%d"),
            today.strftime("%Y%m%d"),
        )
        opened = frame[frame["is_open"] == 1]
        if opened.empty:
            raise TushareUnavailable("交易日历没有返回开放交易日")
        return str(opened.sort_values("cal_date").iloc[-1]["cal_date"])
