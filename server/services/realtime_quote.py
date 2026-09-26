"""免费实时行情辅助源。

Tushare 负责历史行情和规则计算；腾讯行情只用于盘中展示最新价和涨跌幅。
接口失败时由业务层回退到 Tushare 最近交易日收盘数据。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable
from urllib.parse import quote
from urllib.request import Request, urlopen


class RealtimeQuoteClient:
    """读取腾讯公开行情接口，不保存用户信息和密钥。"""

    endpoint = "https://qt.gtimg.cn/q="
    sina_endpoint = "https://hq.sinajs.cn/list="
    eastmoney_endpoint = "https://push2.eastmoney.com/api/qt/stock/get"

    @staticmethod
    def _market_code(ts_code: str) -> str:
        symbol, _, market = str(ts_code).upper().partition(".")
        prefix = {"SZ": "sz", "SH": "sh", "BJ": "bj"}.get(market, market.lower())
        return f"{prefix}{symbol}"

    def fetch_many(self, ts_codes: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        codes = [str(code) for code in ts_codes if code]
        if not codes:
            return {}
        # 单次请求控制在 100 只，避免公开接口 URL 过长或被限流。
        result: Dict[str, Dict[str, Any]] = {}
        for offset in range(0, len(codes), 100):
            batch = codes[offset:offset + 100]
            for parser in (self._fetch_tencent, self._fetch_sina, self._fetch_eastmoney):
                try:
                    quotes = parser(batch)
                except Exception:
                    quotes = {}
                for code, quote_data in quotes.items():
                    result.setdefault(code, quote_data)
                if all(code.upper() in result for code in batch):
                    break
        return result

    @staticmethod
    def _normalise_time(value: str) -> str:
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").isoformat()
        except ValueError:
            return value

    def _fetch_tencent(self, codes: list[str]) -> Dict[str, Dict[str, Any]]:
        query_codes = [self._market_code(code) for code in codes]
        request = Request(self.endpoint + quote(",".join(query_codes), safe=","), headers={"User-Agent": "Stock-Abnormal/1.0"})
        raw = urlopen(request, timeout=4).read().decode("gbk", "ignore")
        result: Dict[str, Dict[str, Any]] = {}
        for match in re.finditer(r'v_([a-z]{2}\d+)="([^"]*)"', raw):
            market_code, payload = match.groups()
            values = payload.split("~")
            if len(values) < 33:
                continue
            symbol = market_code[2:]
            market = {"sz": "SZ", "sh": "SH", "bj": "BJ"}.get(market_code[:2], market_code[:2].upper())
            try:
                current, pre_close, pct_chg = float(values[3]), float(values[4]), float(values[32])
            except (TypeError, ValueError):
                continue
            result[f"{symbol}.{market}"] = {
                "current": current,
                "preClose": pre_close,
                "pctChg": pct_chg,
                "updatedAt": self._normalise_time(values[30] or ""),
                "source": "腾讯行情",
            }
        return result

    def _fetch_sina(self, codes: list[str]) -> Dict[str, Dict[str, Any]]:
        query_codes = [self._market_code(code) for code in codes]
        request = Request(
            self.sina_endpoint + quote(",".join(query_codes), safe=","),
            headers={"User-Agent": "Stock-Abnormal/1.0", "Referer": "https://finance.sina.com.cn/"},
        )
        raw = urlopen(request, timeout=4).read().decode("gbk", "ignore")
        result: Dict[str, Dict[str, Any]] = {}
        for match in re.finditer(r'hq_str_([a-z]{2}\d+)="([^"]*)"', raw):
            market_code, payload = match.groups()
            values = payload.split(",")
            if len(values) < 33:
                continue
            try:
                current, pre_close = float(values[3]), float(values[2])
                if current <= 0 or pre_close <= 0:
                    continue
                pct_chg = (current / pre_close - 1) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            market = {"sz": "SZ", "sh": "SH", "bj": "BJ"}.get(market_code[:2], market_code[:2].upper())
            updated_at = f"{values[30]}T{values[31]}" if len(values) > 31 and values[30] and values[31] else ""
            result[f"{market_code[2:]}.{market}"] = {
                "current": current,
                "preClose": pre_close,
                "pctChg": pct_chg,
                "updatedAt": updated_at,
                "source": "新浪行情",
            }
        return result

    def _fetch_eastmoney(self, codes: list[str]) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for code in codes:
            symbol, _, market = str(code).upper().partition(".")
            market_id = {"SH": "1", "SZ": "0", "BJ": "0"}.get(market, "0")
            url = self.eastmoney_endpoint + "?" + quote(f"secid={market_id}.{symbol}&fields=f43,f57,f58,f60,f86", safe="=&,")
            request = Request(url, headers={"User-Agent": "Stock-Abnormal/1.0", "Referer": "https://quote.eastmoney.com/"})
            payload = json.loads(urlopen(request, timeout=4).read().decode("utf-8"))
            data = payload.get("data") or {}
            current, pre_close = float(data.get("f43") or 0), float(data.get("f60") or 0)
            if current <= 0 or pre_close <= 0:
                continue
            result[f"{symbol}.{market}"] = {
                "current": current,
                "preClose": pre_close,
                "pctChg": (current / pre_close - 1) * 100,
                "updatedAt": str(data.get("f86") or ""),
                "source": "东方财富行情",
            }
        return result

    def fetch_one(self, ts_code: str) -> Dict[str, Any] | None:
        return self.fetch_many([ts_code]).get(str(ts_code).upper())
