"""行情业务层：基础资料、单票计算、监控池和异动预测。

所有对外字段都在这里转换成小程序需要的中文展示结构；Tushare 字段不会直接泄漏到前端。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional

from server.core.abnormal_rules import BOARD_RULES, Board, PriceBar, calculate_deviation, detect_ordinary_abnormal, detect_severe_abnormal
from .realtime_quote import RealtimeQuoteClient
from .tushare_client import TushareClient, TushareUnavailable


INDEX_BY_MARKET = {"SSE": "000001.SH", "SZSE": "399001.SZ", "BSE": "899050.BJ"}
BOARD_BY_MARKET = {"主板": Board.MAIN, "创业板": Board.CHINEXT, "科创板": Board.STAR, "北交所": Board.BSE}


def _date(value: Any) -> date:
    return datetime.strptime(str(value), "%Y%m%d").date()


def _pct(value: Any, signed: bool = True) -> str:
    if value is None:
        return "--"
    number = float(value)
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def _board_label(market: Any, symbol: str) -> str:
    text = str(market or "")
    if text in BOARD_BY_MARKET:
        return text
    if symbol.startswith("688"):
        return "科创板"
    if symbol.startswith("300"):
        return "创业板"
    if symbol.startswith("8") or symbol.startswith("4"):
        return "北交所"
    return "主板"


class MarketService:
    """把 Tushare 数据转为产品 API。"""

    def __init__(self, client: Optional[TushareClient] = None) -> None:
        self.client = client or TushareClient()
        self._stocks_cache: Optional[tuple[datetime, List[Dict[str, Any]]]] = None
        self._daily_cache: Dict[tuple[str, str, str], Any] = {}
        self._prediction_cache: Dict[str, tuple[datetime, List[Dict[str, Any]]]] = {}
        self._realtime = RealtimeQuoteClient()

    def _stock_rows(self) -> List[Dict[str, Any]]:
        now = datetime.utcnow()
        if self._stocks_cache and (now - self._stocks_cache[0]).total_seconds() < 21600:
            return self._stocks_cache[1]
        frame = self.client.stock_basic()
        rows: List[Dict[str, Any]] = []
        for item in frame.to_dict("records"):
            name = str(item.get("name") or "")
            symbol = str(item.get("symbol") or "")
            rows.append({
                "ts_code": str(item.get("ts_code") or ""),
                "symbol": symbol,
                "name": name,
                "market": str(item.get("exchange") or item.get("market") or ""),
                "board": _board_label(item.get("market"), symbol),
                "isST": name.upper().startswith("ST") or name.startswith("*ST"),
                "list_date": item.get("list_date"),
            })
        self._stocks_cache = (now, rows)
        return rows

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """按完整代码、代码前缀、完整名称、名称包含排序，只返回首屏。"""

        value = (query or "").strip().lower()
        if not value:
            return {"items": [], "hasMore": False}
        ranked = []
        for index, item in enumerate(self._stock_rows()):
            symbol = item["symbol"].lower()
            ts_code = item["ts_code"].lower()
            name = item["name"].lower()
            rank = None
            if value in (symbol, ts_code):
                rank = 0
            elif symbol.startswith(value) or ts_code.startswith(value):
                rank = 1
            elif name == value:
                rank = 2
            elif value in name:
                rank = 3
            if rank is not None:
                ranked.append((rank, index, item))
        ranked.sort(key=lambda entry: (entry[0], entry[1]))
        return {"items": [entry[2] for entry in ranked[:limit]], "hasMore": len(ranked) > limit}

    def _daily(self, ts_code: str, start_date: str, end_date: str):
        key = (ts_code, start_date, end_date)
        if key not in self._daily_cache:
            self._daily_cache[key] = self.client.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        return self._daily_cache[key]

    def _index_daily(self, ts_code: str, start_date: str, end_date: str):
        """读取并缓存指数行情，避免把指数误当成股票日线。"""

        key = (f"index:{ts_code}", start_date, end_date)
        if key not in self._daily_cache:
            self._daily_cache[key] = self.client.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        return self._daily_cache[key]

    def _bars(self, frame: Any, ts_code: str) -> List[PriceBar]:
        if frame is None or frame.empty:
            return []
        rows = sorted(frame.to_dict("records"), key=lambda item: str(item["trade_date"]))
        return [PriceBar(_date(row["trade_date"]), float(row["close"])) for row in rows if row.get("close") is not None and float(row["close"]) > 0]

    def _aligned_bars(self, stock_frame: Any, index_frame: Any, stock_code: str, index_code: str):
        """按交易日内连接股票和指数，避免停牌或缺失数据造成错位。"""

        stock_by_date = {bar.trade_date: bar for bar in self._bars(stock_frame, stock_code)}
        index_by_date = {bar.trade_date: bar for bar in self._bars(index_frame, index_code)}
        dates = sorted(set(stock_by_date).intersection(index_by_date))
        return [stock_by_date[item] for item in dates], [index_by_date[item] for item in dates]

    def _stock(self, ts_code: str) -> Dict[str, Any]:
        rows = [item for item in self._stock_rows() if item["ts_code"] == ts_code or item["symbol"] == ts_code]
        if not rows:
            raise ValueError("未找到对应的 A 股股票")
        return rows[0]

    def _future_trade_dates(self, latest: str, count: int = 10) -> List[str]:
        """根据交易所日历生成 T+1 至 T+N，页面不再使用静态日期。"""

        latest_day = datetime.strptime(latest, "%Y%m%d")
        end = latest_day + timedelta(days=30)
        frame = self.client.trade_cal(
            (latest_day + timedelta(days=1)).strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
        )
        opened = sorted(str(row["cal_date"]) for row in frame.to_dict("records") if int(row.get("is_open", 0)) == 1)
        return [datetime.strptime(value, "%Y%m%d").strftime("%m-%d") for value in opened[:count]]

    def detail(self, ts_code: str) -> Dict[str, Any]:
        """返回单股当前状态、四项预警和模拟计算基础数据。"""

        stock = self._stock(ts_code)
        latest = self.client.latest_trade_date()
        start = (datetime.strptime(latest, "%Y%m%d") - timedelta(days=70)).strftime("%Y%m%d")
        stock_frame = self._daily(stock["ts_code"], start, latest)
        index_code = INDEX_BY_MARKET.get(stock["market"], "000001.SH")
        index_frame = self._index_daily(index_code, start, latest)
        stock_bars, index_bars = self._aligned_bars(stock_frame, index_frame, stock["ts_code"], index_code)
        board = BOARD_BY_MARKET.get(stock["board"], Board.MAIN)
        deviations: Dict[int, Optional[float]] = {}
        for window in (3, 10, 30):
            deviations[window] = None
            if len(stock_bars) >= window + 1:
                deviations[window] = calculate_deviation(stock_bars, index_bars, window).deviation
        severe = detect_severe_abnormal(stock_bars, index_bars, board) if len(stock_bars) >= 2 else None
        status = "安全"
        status_class = "safe"
        if severe and severe.triggered:
            status, status_class = "严重异动", "severe"
        elif any(value is not None and abs(value) >= (30 if window == 3 else 100 if window == 10 else 200) for window, value in deviations.items()):
            status, status_class = "风险提示", "risk"
        latest_row = sorted(stock_frame.to_dict("records"), key=lambda item: str(item["trade_date"]))[-1] if not stock_frame.empty else {}
        quote = self._realtime.fetch_one(stock["ts_code"])
        current_price = quote["current"] if quote else latest_row.get("close")
        current_change = quote["pctChg"] if quote else latest_row.get("pct_chg")
        return {
            **stock,
            "status": status,
            "statusClass": status_class,
            "statusIcon": "!" if status_class != "safe" else "✓",
            "currentPrice": f"{float(current_price):.2f}" if current_price is not None else "--",
            "change": _pct(current_change),
            "tradeDate": latest,
            "futureTradeDates": self._future_trade_dates(latest),
            "warnings": [
                {"title": "3 日偏离", "value": _pct(deviations[3]), "target": "阈值 ±30%", "className": "risk" if deviations[3] is not None and abs(deviations[3]) >= 30 else "safe"},
                {"title": "10 日偏离", "value": _pct(deviations[10]), "target": "阈值 +100% / -50%", "className": "risk" if deviations[10] is not None and abs(deviations[10]) >= 100 else "neutral"},
                {"title": "30 日偏离", "value": _pct(deviations[30]), "target": "阈值 +200% / -70%", "className": "risk" if deviations[30] is not None and abs(deviations[30]) >= 200 else "neutral"},
                {"title": "10 日同向", "value": "", "up": str(severe.same_direction_count if severe else 0), "down": "0", "target": "按板块规则统计", "className": "risk" if severe and severe.same_direction_count else "safe"},
            ],
            "alerts": [],
            "dataQuality": {"source": "tushare+腾讯行情" if quote else "tushare", "tradeDate": latest, "isComplete": len(stock_bars) >= 31, "intraday": bool(quote), "quoteUpdatedAt": quote.get("updatedAt") if quote else ""},
        }

    def _prediction_items(self, scope: str, force: bool = False) -> List[Dict[str, Any]]:
        """扫描全市场。日行情接口按交易日批量拉取，缓存后避免重复请求。"""

        now = datetime.utcnow()
        cached = self._prediction_cache.get(scope)
        if cached and not force and (now - cached[0]).total_seconds() < 60:
            return cached[1]

        latest = self.client.latest_trade_date()
        latest_day = datetime.strptime(latest, "%Y%m%d")
        start = (latest_day - timedelta(days=70)).strftime("%Y%m%d")
        stocks = self._stock_rows()
        # Tushare daily 支持 trade_date 全市场查询；按日期拉取并按股票聚合。
        trade_dates = self.client.trade_cal(start, latest)
        open_dates = [str(item["cal_date"]) for item in trade_dates.to_dict("records") if int(item["is_open"]) == 1][-35:]
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for trade_date in open_dates:
            frame = self.client.daily(trade_date=trade_date)
            for row in frame.to_dict("records"):
                grouped.setdefault(str(row.get("ts_code")), []).append(row)
        stock_index = {item["ts_code"]: item for item in stocks}
        result: List[Dict[str, Any]] = []
        for ts_code, rows in grouped.items():
            stock = stock_index.get(ts_code)
            if not stock or not rows:
                continue
            board = BOARD_BY_MARKET.get(stock["board"], Board.MAIN)
            index_code = INDEX_BY_MARKET.get(stock["market"], "000001.SH")
            index_frame = self._index_daily(index_code, start, latest)
            stock_bars = self._bars(type("Frame", (), {"empty": not rows, "to_dict": lambda self, _=None: rows})(), ts_code)
            index_bars = self._bars(index_frame, index_code)
            index_by_date = {bar.trade_date: bar for bar in index_bars}
            aligned = [(bar, index_by_date[bar.trade_date]) for bar in stock_bars if bar.trade_date in index_by_date]
            if len(aligned) < 4:
                continue
            stock_bars = [pair[0] for pair in aligned]
            index_bars = [pair[1] for pair in aligned]
            window = 3
            if len(stock_bars) >= 31 and board in (Board.MAIN, Board.CHINEXT, Board.STAR, Board.BSE):
                window = 30 if scope == "next_day" else 10
            elif len(stock_bars) >= 11:
                window = 10
            elif len(stock_bars) >= 4:
                window = 3
            deviation = calculate_deviation(stock_bars, index_bars, window).deviation
            threshold = BOARD_BY_MARKET.get(stock["board"], Board.MAIN)
            target = 200 if window == 30 else 100 if window == 10 else BOARD_RULES[threshold].ordinary_deviation
            distance = abs(target) - abs(deviation)
            if distance > 20:
                continue
            last = sorted(rows, key=lambda item: str(item["trade_date"]))[-1]
            result.append({
                **stock,
                "scope": "当日" if scope == "today" else "次日",
                "change": _pct(last.get("pct_chg")),
                "trigger": f"上涨 ≥ {max(0.01, distance):.2f}%" if distance > 0 else "已达到阈值",
                "deviation": f"{window}日 {_pct(deviation)}",
                "rule": f"连续{window}个交易日内日收盘价格涨跌幅偏离值累计达到±{target:.0f}%",
                "tradeDate": latest,
            })
        quotes = self._realtime.fetch_many(item["ts_code"] for item in result)
        for item in result:
            quote = quotes.get(item["ts_code"])
            if quote:
                item["change"] = _pct(quote["pctChg"])
                item["quoteUpdatedAt"] = quote.get("updatedAt", "")
                item["quoteSource"] = quote.get("source", "腾讯行情")
        result.sort(key=lambda item: abs(float(item["deviation"].split()[-1].replace("日", "").replace("%", ""))) if item.get("deviation") else 999, reverse=True)
        output = result[:100]
        self._prediction_cache[scope] = (now, output)
        return output

    def predictions(self, scope: str, force: bool = False) -> Dict[str, Any]:
        if scope not in ("today", "next_day"):
            raise ValueError("scope 必须是 today 或 next_day")
        items = self._prediction_items(scope, force=force)
        has_realtime = any(item.get("quoteSource") for item in items)
        return {"scope": scope, "items": items, "updatedAt": datetime.now().strftime("%H:%M:%S"), "dataQuality": {"source": "tushare+腾讯行情" if has_realtime else "tushare", "intraday": has_realtime}}

    def monitor(self, status: str = "current", monitor_type: str = "all") -> Dict[str, Any]:
        """监控池接口。

        交易所公告监控并非 Tushare 的完整标准接口，当前以可复核的规则计算结果生成当前记录；
        后续可接入公告源补充监控期起止日期，接口字段已预留 source/sourceUrl。
        """

        if status not in ("current", "history"):
            raise ValueError("status 必须是 current 或 history")
        items: List[Dict[str, Any]] = []
        # 个人开发者版本先对预测候选进行单票复核，避免无界面触发数千次请求。
        for item in self._prediction_items("today")[:50]:
            is_severe_30 = "200" in item["rule"]
            is_severe_10 = not is_severe_30 and "100" in item["rule"]
            monitor_key = "severe_30d" if is_severe_30 else "severe_10d" if is_severe_10 else "ordinary"
            if monitor_type not in ("all", monitor_key, "severe") and not (monitor_type == "risk" and monitor_key == "ordinary"):
                continue
            window_days = 30 if is_severe_30 else 10 if is_severe_10 else 3
            future_dates = self._future_trade_dates(item["tradeDate"], window_days)
            monitor_end = future_dates[window_days - 1] if len(future_dates) >= window_days else "待交易所公告确认"
            items.append({
                **item,
                "monitorKey": f"{item['ts_code']}-{monitor_key}",
                "riskLevel": "severe" if monitor_key != "ordinary" else "risk",
                "monitorType": "30日严重异动" if is_severe_30 else "10日严重异动" if is_severe_10 else "风险提示",
                "monitorStart": item["tradeDate"],
                "monitorEnd": item["tradeDate"] if status == "history" else monitor_end,
                "days": 0 if status == "history" else window_days,
                "isHistory": status == "history",
                "source": "tushare 规则计算",
            })
        if status == "history":
            items = []
        return {"tab": status, "items": items, "updatedAt": datetime.now().strftime("%H:%M:%S"), "dataQuality": {"source": "tushare+规则计算", "isOfficialMonitorPeriod": False}}
