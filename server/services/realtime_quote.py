"""免费实时行情辅助源。

Tushare 负责历史行情和规则计算；腾讯行情只用于盘中展示最新价和涨跌幅。
接口失败时由业务层回退到 Tushare 最近交易日收盘数据。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable
from urllib.parse import quote
from urllib.request import Request, urlopen


class RealtimeQuoteClient:
    """读取腾讯公开行情接口，不保存用户信息和密钥。"""

    endpoint = "https://qt.gtimg.cn/q="

    @staticmethod
    def _market_code(ts_code: str) -> str:
        symbol, _, market = str(ts_code).upper().partition(".")
        prefix = {"SZ": "sz", "SH": "sh", "BJ": "bj"}.get(market, market.lower())
        return f"{prefix}{symbol}"

    def fetch_many(self, ts_codes: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        codes = [str(code) for code in ts_codes if code]
        if not codes:
            return {}
        query_codes = [self._market_code(code) for code in codes]
        request = Request(
            self.endpoint + quote(",".join(query_codes), safe=","),
            headers={"User-Agent": "Stock-Abnormal/1.0"},
        )
        try:
            raw = urlopen(request, timeout=4).read().decode("gbk", "ignore")
        except Exception:
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for match in re.finditer(r'v_([a-z]{2}\d+)="([^"]*)"', raw):
            market_code, payload = match.groups()
            values = payload.split("~")
            if len(values) < 33:
                continue
            symbol = market_code[2:]
            market = {"sz": "SZ", "sh": "SH", "bj": "BJ"}.get(market_code[:2], market_code[:2].upper())
            try:
                current = float(values[3])
                pre_close = float(values[4])
                pct_chg = float(values[32])
            except (TypeError, ValueError):
                continue
            updated_at = values[30] or ""
            try:
                updated_at = datetime.strptime(updated_at, "%Y%m%d%H%M%S").isoformat()
            except ValueError:
                pass
            result[f"{symbol}.{market}"] = {
                "current": current,
                "preClose": pre_close,
                "pctChg": pct_chg,
                "updatedAt": updated_at,
                "source": "腾讯行情",
            }
        return result

    def fetch_one(self, ts_code: str) -> Dict[str, Any] | None:
        return self.fetch_many([ts_code]).get(str(ts_code).upper())
