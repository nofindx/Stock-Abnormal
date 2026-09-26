"""Tushare 数据源适配器。

密钥只在后端进程中读取，微信小程序只访问本服务的 JSON API。
这里不打印 token，也不把 token 写入响应、缓存或日志。
"""

from __future__ import annotations

import os
import json
from datetime import date, timedelta
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

        try:
            return getattr(self._api(), method)(**kwargs)
        except Exception as exc:  # Tushare 异常类型在不同版本不一致。
            message = str(exc)
            if "token" in message.lower() or "积分" in message or "权限" in message:
                raise TushareUnavailable("Tushare 授权或接口权限不可用") from exc
            raise TushareUnavailable(f"Tushare {method} 请求失败") from exc

    def stock_basic(self):
        """读取全部上市 A 股基础资料。"""

        return self.call(
            "stock_basic",
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,market,exchange,list_date",
        )

    def trade_cal(self, start_date: str, end_date: str):
        """读取交易日历。"""

        return self.call("trade_cal", exchange="SSE", start_date=start_date, end_date=end_date)

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
        return self.call("daily", **params)

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
        return self.call("index_daily", **params)

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
        return self.call("daily_basic", **params)

    def latest_trade_date(self) -> str:
        """返回最近一个交易日，优先查历年最近 20 天。"""

        today = date.today()
        frame = self.trade_cal(
            (today - timedelta(days=20)).strftime("%Y%m%d"),
            today.strftime("%Y%m%d"),
        )
        opened = frame[frame["is_open"] == 1]
        if opened.empty:
            raise TushareUnavailable("交易日历没有返回开放交易日")
        return str(opened.sort_values("cal_date").iloc[-1]["cal_date"])
