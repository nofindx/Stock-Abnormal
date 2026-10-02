"""行情业务层：基础资料、单票计算、监控池和异动预测。

所有对外字段都在这里转换成小程序需要的中文展示结构；Tushare 字段不会直接泄漏到前端。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8 本地开发环境兼容回退。
    ZoneInfo = None

from server.core.abnormal_rules import BOARD_RULES, Board, PriceBar, calculate_deviation, detect_ordinary_abnormal, detect_severe_abnormal
from .realtime_quote import RealtimeQuoteClient
from .market_calc_repository import MarketCalcRepository
from .tushare_client import TushareClient, TushareUnavailable


INDEX_BY_MARKET = {"SSE": "000001.SH", "SZSE": "399001.SZ", "BSE": "899050.BJ"}
INDEX_BY_BOARD = {"创业板": "399006.SZ", "科创板": "000688.SH", "北交所": "899050.BJ"}
BOARD_BY_MARKET = {"主板": Board.MAIN, "创业板": Board.CHINEXT, "科创板": Board.STAR, "北交所": Board.BSE}
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone(timedelta(hours=8))
# 停牌/复牌重置、上涨同向计数和预测阶段切换属于同一套规则口径。
# 版本变化会触发服务启动时用现有基础数据重建预测数据集。
PREDICTION_RULE_VERSION = "2026-09-upward-v8-suspension-reset"

# GBK 区位表不依赖第三方拼音包，适合云托管的轻量搜索场景。
_PINYIN_RANGES = (
    (0xB0A1, "A"), (0xB0C5, "B"), (0xB2C1, "C"), (0xB4EE, "D"),
    (0xB6EA, "E"), (0xB7A2, "F"), (0xB8C1, "G"), (0xB9FE, "H"),
    (0xBBF7, "J"), (0xBFA6, "K"), (0xC0AC, "L"), (0xC2E8, "M"),
    (0xC4C3, "N"), (0xC5B6, "O"), (0xC5BE, "P"), (0xC6DA, "Q"),
    (0xC8BB, "R"), (0xC8F5, "S"), (0xCBF9, "T"), (0xCDD9, "W"),
    (0xCEF3, "X"), (0xD1B9, "Y"), (0xD4D1, "Z"),
)


def _name_initials(value: str) -> str:
    """返回中文名称首字母，例如“博杰股份” -> “BJGF”。"""

    result: List[str] = []
    for char in str(value or ""):
        if "A" <= char.upper() <= "Z":
            result.append(char.upper())
            continue
        if "0" <= char <= "9":
            result.append(char)
            continue
        try:
            encoded = char.encode("gbk")
            if len(encoded) != 2:
                continue
            code = encoded[0] * 256 + encoded[1]
            initial = ""
            for threshold, letter in _PINYIN_RANGES:
                if code >= threshold:
                    initial = letter
                else:
                    break
            result.append(initial or "#")
        except UnicodeEncodeError:
            continue
    return "".join(result)


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


def _index_code_for_stock(stock: Dict[str, Any]) -> str:
    """按股票板块选择对应指数；主板再区分沪深市场。"""

    board = str(stock.get("board") or "主板")
    if board in INDEX_BY_BOARD:
        return INDEX_BY_BOARD[board]
    return INDEX_BY_MARKET.get(str(stock.get("market") or ""), "000001.SH")


class MarketService:
    """把 Tushare 数据转为产品 API。"""

    def __init__(self, client: Optional[TushareClient] = None) -> None:
        self.client = client or TushareClient()
        self._calc_repository = MarketCalcRepository()
        self._stocks_cache: Optional[tuple[datetime, List[Dict[str, Any]]]] = None
        self._market_context_cache: Optional[tuple[datetime, Dict[str, Any]]] = None
        self._daily_cache: Dict[tuple[str, str, str], Any] = {}
        self._prediction_cache: Dict[str, tuple[datetime, List[Dict[str, Any]]]] = {}
        self._prediction_last_error: Dict[str, str] = {}
        self._prediction_trade_date: Dict[str, str] = {}
        self._prediction_state_cache: Optional[tuple[datetime, Dict[str, Any]]] = None
        self._prediction_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prediction")
        self._prediction_ephemeral: Dict[str, tuple[datetime, List[Dict[str, Any]]]] = {}
        self._intraday_refresh_futures: Dict[str, Any] = {}
        self._intraday_refresh_lock = Lock()
        self._prediction_rebuild_lock = Lock()
        self._realtime = RealtimeQuoteClient()
        self._market_scheduler_stop = Event()
        self._market_scheduler: Optional[Thread] = None
        self._market_update_lock = Lock()
        self._market_last_formal_attempt = ""
        self._market_update_error = ""
        # 监控池由 OfficialMonitorService 公告快照链路独立负责。

    def start_scheduler(self) -> None:
        """启动单股计算基础数据的每日更新任务。"""

        if self._market_scheduler and self._market_scheduler.is_alive():
            return
        self._market_scheduler_stop.clear()
        self._market_scheduler = Thread(target=self._market_scheduler_loop, name="market-calc", daemon=True)
        self._market_scheduler.start()
        # 规则代码发布后，持久化预测结果可能仍是旧口径。启动时只读取
        # MySQL 已有的基础向量重建结果集，不请求行情、不触发全市场采集。
        Thread(target=self._rebuild_predictions_if_rule_changed, name="prediction-rebuild", daemon=True).start()

    def _rebuild_predictions_if_rule_changed(self) -> None:
        if not self._calc_repository.available:
            return
        with self._prediction_rebuild_lock:
            try:
                records = [self._calc_repository.get_prediction(scope) for scope in ("today", "next_day")]
                if records and all(record and record.get("rule_version") == PREDICTION_RULE_VERSION for record in records):
                    return
                stage = self._latest_data_stage() or "formal"
                if self._calc_repository.all_calculations() and self._calc_repository.all_indexes():
                    self._generate_prediction_datasets(stage)
            except Exception as exc:  # noqa: BLE001 - 重建失败不影响旧数据读取
                self._prediction_last_error["today"] = f"预测规则重建失败：{exc}"

    def close(self) -> None:
        """停止每日更新和预测线程池。"""

        self._market_scheduler_stop.set()
        self._prediction_executor.shutdown(wait=False)

    def _stock_rows(self, force: bool = False) -> List[Dict[str, Any]]:
        now = datetime.now(SHANGHAI_TZ)
        if not force and self._stocks_cache and (now - self._stocks_cache[0]).total_seconds() < 21600:
            return self._stocks_cache[1]
        if not force and self._calc_repository.available:
            try:
                stored = self._calc_repository.list_stocks()
                if stored:
                    rows = [self._normalise_stock_row(item) for item in stored]
                    self._stocks_cache = (now, rows)
                    return rows
            except Exception as exc:  # noqa: BLE001 - 数据库不可用时回退 Tushare
                self._market_update_error = f"MySQL 读取失败：{exc}"
        try:
            frame = self.client.stock_basic(force=force)
        except TypeError:
            # 兼容测试或旧数据源适配器尚未支持 force 参数的情况。
            frame = self.client.stock_basic()
        rows: List[Dict[str, Any]] = []
        for item in frame.to_dict("records"):
            rows.append(self._normalise_stock_row(item))
        self._stocks_cache = (now, rows)
        return rows

    @staticmethod
    def _normalise_stock_row(item: Dict[str, Any]) -> Dict[str, Any]:
        name = str(item.get("name") or "")
        symbol = str(item.get("symbol") or "")
        computed_initials = _name_initials(name)
        return {
            "ts_code": str(item.get("ts_code") or ""),
            "symbol": symbol,
            "name": name,
            "market": str(item.get("exchange") or item.get("market") or ""),
            "board": str(item.get("board") or _board_label(item.get("market") or item.get("exchange"), symbol)),
            "isST": bool(item.get("isST", name.upper().startswith("ST") or name.startswith("*ST"))),
            "nameInitials": computed_initials or str(item.get("nameInitials") or item.get("name_initials") or ""),
            "list_date": item.get("list_date"),
            "list_status": item.get("list_status", "L"),
        }

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """按代码、完整名称、名称首字母和名称包含排序，只返回首屏。"""

        value = (query or "").strip().lower()
        if not value:
            return {"items": [], "hasMore": False}
        ranked = []
        for index, item in enumerate(self._stock_rows()):
            symbol = item["symbol"].lower()
            ts_code = item["ts_code"].lower()
            name = item["name"].lower()
            initials = item["nameInitials"].lower()
            rank = None
            if value in (symbol, ts_code):
                rank = 0
            elif symbol.startswith(value) or ts_code.startswith(value):
                rank = 1
            elif name == value:
                rank = 2
            elif initials == value:
                rank = 3
            elif initials.startswith(value):
                rank = 4
            elif value in name:
                rank = 5
            if rank is not None:
                ranked.append((rank, index, item))
        ranked.sort(key=lambda entry: (entry[0], entry[1]))
        return {"items": [entry[2] for entry in ranked[:limit]], "hasMore": len(ranked) > limit}

    def _market_context(self) -> Dict[str, Any]:
        """缓存最近 30 个交易日和板块指数，详情请求复用该上下文。"""

        now = datetime.now(SHANGHAI_TZ)
        if self._market_context_cache and (now - self._market_context_cache[0]).total_seconds() < 300:
            return self._market_context_cache[1]
        latest = self.client.latest_trade_date()
        latest_day = datetime.strptime(latest, "%Y%m%d")
        calendar_start = (latest_day - timedelta(days=60)).strftime("%Y%m%d")
        calendar = self.client.trade_cal(calendar_start, latest)
        open_dates = sorted(
            str(row["cal_date"]) for row in calendar.to_dict("records")
            if int(row.get("is_open", 0)) == 1
        )[-31:]
        if len(open_dates) < 2:
            raise TushareUnavailable("交易日历不足 30 个交易日")
        start_date = open_dates[0]
        index_frames = {
            code: self._index_daily(code, start_date, latest)
            for code in sorted(set(INDEX_BY_MARKET.values()) | set(INDEX_BY_BOARD.values()))
        }
        context = {
            "latest": latest,
            "tradeDates": open_dates,
            "start": start_date,
            "indexFrames": index_frames,
            "indexReturns": {
                code: {
                    days: calculate_deviation(self._bars(frame, code), self._bars(frame, code), days).stock_return
                    if len(self._bars(frame, code)) >= days + 1 else None
                    for days in (3, 10, 30)
                }
                for code, frame in index_frames.items()
            },
        }
        self._market_context_cache = (now, context)
        return context

    def _daily(self, ts_code: str, start_date: str, end_date: str):
        key = (ts_code, start_date, end_date)
        if key not in self._daily_cache:
            self._daily_cache[key] = self.client.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        return self._daily_cache[key]

    def _daily_with_turnover(self, ts_code: str, start_date: str, end_date: str):
        """兼容旧调用名；单股计算不再读取 daily_basic 或换手率。"""

        return self._daily(ts_code, start_date, end_date).copy()

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

        return [datetime.strptime(value, "%Y%m%d").strftime("%m-%d") for value in self._future_trade_date_values(latest, count)]

    def _future_trade_date_values(self, latest: str, count: int = 10) -> List[str]:
        """返回完整 YYYYMMDD 交易日，供监控期和剩余交易日复用。"""

        latest_day = datetime.strptime(latest, "%Y%m%d")
        end = latest_day + timedelta(days=30)
        frame = self.client.trade_cal(
            (latest_day + timedelta(days=1)).strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
        )
        opened = sorted(str(row["cal_date"]) for row in frame.to_dict("records") if int(row.get("is_open", 0)) == 1)
        return opened[:count]

    @staticmethod
    def _display_trade_date(value: str) -> str:
        return datetime.strptime(value, "%Y%m%d").strftime("%m-%d")

    @staticmethod
    def _quote_date(quote: Optional[Dict[str, Any]]) -> Optional[date]:
        value = str((quote or {}).get("updatedAt") or "")
        if len(value) >= 10:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
        return None

    @classmethod
    def _merge_realtime_bars(cls, stock_bars: List[PriceBar], index_bars: List[PriceBar], stock_quote: Optional[Dict[str, Any]], index_quote: Optional[Dict[str, Any]]):
        """将同一行情时点的股票和板块指数报价并入收盘序列。"""

        if not stock_quote or not index_quote:
            return stock_bars, index_bars
        try:
            stock_close = float(stock_quote.get("current"))
            index_close = float(index_quote.get("current"))
        except (TypeError, ValueError):
            return stock_bars, index_bars
        if stock_close <= 0 or index_close <= 0:
            return stock_bars, index_bars
        quote_date = cls._quote_date(stock_quote) or cls._quote_date(index_quote)
        if not quote_date:
            return stock_bars, index_bars
        stock_result = list(stock_bars)
        index_result = list(index_bars)
        if stock_result and stock_result[-1].trade_date == quote_date and index_result and index_result[-1].trade_date == quote_date:
            stock_result[-1] = PriceBar(quote_date, stock_close, stock_result[-1].turnover_rate)
            index_result[-1] = PriceBar(quote_date, index_close, index_result[-1].turnover_rate)
        elif not stock_result or quote_date > stock_result[-1].trade_date:
            stock_result.append(PriceBar(quote_date, stock_close))
            index_result.append(PriceBar(quote_date, index_close))
        return stock_result, index_result

    @staticmethod
    def _build_alerts(deviations: Dict[int, Optional[float]], severe: Any, board: Board) -> List[Dict[str, Any]]:
        rule = BOARD_RULES[board]
        thresholds = {
            3: (rule.ordinary_deviation, -rule.ordinary_deviation),
            10: (rule.severe_10d_threshold.up, rule.severe_10d_threshold.down),
            30: (rule.severe_30d_threshold.up, rule.severe_30d_threshold.down),
        }
        alerts: List[Dict[str, Any]] = []
        for days in (3, 10, 30):
            value = deviations.get(days)
            up, down = thresholds[days]
            target = up if (value is None or value >= 0) else down
            distance = abs(target - value) if value is not None else None
            triggered = value is not None and ((value >= up) or (value <= down))
            progress = min(100, abs(value) / max(abs(target), 0.01) * 100) if value is not None else 0
            alerts.append({
                "title": f"{days} 日偏离",
                "forecast": "已触发" if triggered else (f"还差 {distance:.2f}%" if distance is not None else "数据不足"),
                "detail": f"当前 {_pct(value)}，阈值 {up:+.0f}% / {down:+.0f}%" if value is not None else "暂无足够交易日数据",
                "progressStyle": f"width:{progress:.1f}%",
                "className": "risk" if triggered else "safe" if value is not None else "neutral",
            })
        count = severe.same_direction_count if severe else 0
        limit = severe.same_direction_threshold if severe else rule.severe_same_direction_count
        progress = min(100, count / max(limit, 1) * 100)
        alerts.append({
            "title": "10 日同向",
            "forecast": "已触发" if count >= limit else f"还差 {max(0, limit - count)} 次",
            "detail": f"上涨 {severe.up_count if severe else 0} 次 / 下跌 {severe.down_count if severe else 0} 次，阈值 {limit} 次",
            "progressStyle": f"width:{progress:.1f}%",
            "className": "risk" if count >= limit else "safe",
        })
        return alerts

    @staticmethod
    def _simulation_returns(stock_bars: List[PriceBar], index_bars: List[PriceBar]) -> Dict[str, List[float]]:
        """提供最近 30 个交易日收益率，让前端模拟时只增量追加假设值。"""

        stock_returns: List[float] = []
        index_returns: List[float] = []
        for previous, current in zip(stock_bars, stock_bars[1:]):
            stock_returns.append((current.close / previous.close - 1) * 100)
        for previous, current in zip(index_bars, index_bars[1:]):
            index_returns.append((current.close / previous.close - 1) * 100)
        return {"stock": stock_returns[-30:], "index": index_returns[-30:]}

    @staticmethod
    def _vector_entries(value: Any) -> List[Dict[str, Any]]:
        """统一解析数据库中的滚动收益率；新格式带日期，旧格式仍可读。"""

        if isinstance(value, str):
            try:
                import json
                value = json.loads(value)
            except (TypeError, ValueError):
                value = []
        entries: List[Dict[str, Any]] = []
        for index, item in enumerate(value or []):
            if isinstance(item, dict):
                raw_date = item.get("date") or item.get("tradeDate") or ""
                raw_return = item.get("return", item.get("value"))
            else:
                raw_date = ""
                raw_return = item
            try:
                number = float(raw_return)
            except (TypeError, ValueError):
                continue
            entries.append({"date": str(raw_date), "return": number, "order": index})
        return entries[-30:]

    @staticmethod
    def _date_key(value: Any) -> str:
        """将收益率向量中的 YYYY-MM-DD/yyyymmdd 统一成可排序键。"""

        return str(value or "").replace("-", "")[:8]

    @classmethod
    def _trim_vectors_after_suspension(
        cls,
        stock: List[Dict[str, Any]],
        index: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """复牌后从新的有效区间重新计算，避免把停牌前涨幅带入规则。

        日线接口通常不会为停牌日返回股票记录，而指数仍会有交易日记录。
        因此只要股票在指数交易日序列中出现缺口，缺口后的第一条股票数据
        就是新的计算起点。尾部仍停牌时不伪造结果，调用方会跳过预测。
        """

        stock_entries = list(stock or [])
        index_entries = list(index or [])
        stock_dates = {cls._date_key(item.get("date")) for item in stock_entries if item.get("date")}
        index_dates = sorted({cls._date_key(item.get("date")) for item in index_entries if item.get("date")})
        ordered_stock = sorted(
            (item for item in stock_entries if item.get("date")),
            key=lambda item: cls._date_key(item.get("date")),
        )
        ordered_index = sorted(
            (item for item in index_entries if item.get("date")),
            key=lambda item: cls._date_key(item.get("date")),
        )
        if not ordered_stock or not ordered_index:
            return stock_entries, index_entries, {"resetDate": "", "suspended": False}

        stock_latest = cls._date_key(ordered_stock[-1].get("date"))
        index_latest = cls._date_key(ordered_index[-1].get("date"))
        missing = [day for day in index_dates if day < stock_latest and day not in stock_dates]
        reset_date = ""
        if missing:
            last_gap = missing[-1]
            resumed = [cls._date_key(item.get("date")) for item in ordered_stock if cls._date_key(item.get("date")) > last_gap]
            reset_date = resumed[0] if resumed else ""

        if reset_date:
            stock_entries = [item for item in ordered_stock if cls._date_key(item.get("date")) >= reset_date]
            index_entries = [item for item in ordered_index if cls._date_key(item.get("date")) >= reset_date]
        else:
            stock_entries = ordered_stock
            index_entries = ordered_index
        return stock_entries[-30:], index_entries[-30:], {
            "resetDate": reset_date,
            "suspended": bool(stock_latest < index_latest),
        }

    @classmethod
    def _trim_bars_after_suspension(
        cls,
        stock: List[PriceBar],
        index: List[PriceBar],
    ) -> tuple[List[PriceBar], List[PriceBar], Dict[str, Any]]:
        """PriceBar 版本的复牌重置，供无 MySQL 的冷启动路径使用。"""

        stock_entries = [{"date": item.trade_date.isoformat(), "bar": item} for item in stock]
        index_entries = [{"date": item.trade_date.isoformat(), "bar": item} for item in index]
        stock_trimmed, index_trimmed, meta = cls._trim_vectors_after_suspension(stock_entries, index_entries)
        return (
            [item["bar"] for item in stock_trimmed],
            [item["bar"] for item in index_trimmed],
            meta,
        )

    @staticmethod
    def _status_for_metrics(
        deviations: Dict[int, Optional[float]],
        same_up: int,
        rule: Any,
    ) -> tuple[str, str]:
        """统一股票状态：正常（绿）/ 警示（黄）/ 触及（红）。"""

        if deviations.get(30) is not None and rule.severe_30d_threshold.triggered(deviations[30]):
            return "触及", "severe"
        if deviations.get(10) is not None and rule.severe_10d_threshold.triggered(deviations[10]):
            return "触及", "severe"
        # 3 日普通异动和 10 日上涨同向属于警示，不升级为红色严重异常。
        if same_up >= rule.severe_same_direction_count:
            return "警示", "risk"
        if deviations.get(3) is not None and abs(deviations[3]) >= rule.ordinary_deviation:
            return "警示", "risk"
        return "正常", "safe"

    @staticmethod
    def _vector_numbers(entries: List[Dict[str, Any]]) -> List[float]:
        return [float(item["return"]) for item in entries]

    @staticmethod
    def _compound(values: Iterable[float]) -> float:
        result = 1.0
        for value in values:
            result *= 1.0 + float(value) / 100.0
        return (result - 1.0) * 100.0

    @classmethod
    def _vector_deviation(cls, stock: List[Dict[str, Any]], index: List[Dict[str, Any]], window: int) -> Optional[float]:
        if not stock or not index:
            return None
        stock_dates = {item["date"] for item in stock if item.get("date")}
        index_dates = {item["date"] for item in index if item.get("date")}
        if stock_dates and index_dates:
            dates = sorted(stock_dates & index_dates)[-window:]
            stock_map = {item["date"]: item["return"] for item in stock}
            index_map = {item["date"]: item["return"] for item in index}
            if len(dates) < window:
                return None
            return cls._compound(stock_map[item] for item in dates) - cls._compound(index_map[item] for item in dates)
        if len(stock) < window or len(index) < window:
            return None
        return cls._compound(cls._vector_numbers(stock[-window:])) - cls._compound(cls._vector_numbers(index[-window:]))

    @classmethod
    def _best_vector_deviation(cls, stock: List[Dict[str, Any]], index: List[Dict[str, Any]], max_window: int) -> tuple[Optional[float], Optional[int]]:
        """取规则回看窗口内最接近上涨阈值的有效连续区间。

        交易所规则写的是“连续 N 个交易日内”，实际有效数据允许因边界、
        停牌或指数对齐缺口少少量交易日。10 日只在 8/9/10 日、30 日只在
        27/28/29/30 日区间中取偏离值最大的正向区间，不能用任意 3 日窗口冒充 10/30 日；
        返回值同时带实际区间长度，用于展示“9 日”“28 日”等结果。
        """
        if not stock or not index:
            return None, None
        stock_map = {item.get("date"): float(item.get("return")) for item in stock if item.get("date")}
        index_map = {item.get("date"): float(item.get("return")) for item in index if item.get("date")}
        dates = sorted(set(stock_map) & set(index_map))
        if len(dates) < 3:
            return None, None
        upper = min(max_window, len(dates))
        # 交易所的 N 日是回看上限，不是任意更短的 3 日窗口。
        # 10 日允许 8/9/10；30 日允许 27/28/29/30，兼容边界/停牌/指数缺口。
        lower = 27 if max_window == 30 else max(3, max_window - 2)
        if upper < lower:
            return None, None
        candidates = []
        for window in range(lower, upper + 1):
            value = cls._compound(stock_map[day] for day in dates[-window:]) - cls._compound(index_map[day] for day in dates[-window:])
            candidates.append((value, window))
        positive = [item for item in candidates if item[0] > 0]
        if positive:
            return max(positive, key=lambda item: (item[0], -item[1]))
        return min(candidates, key=lambda item: (item[0], item[1]))

    @classmethod
    def _required_up_percent(cls, stock: List[Dict[str, Any]], index: List[Dict[str, Any]], actual_window: int, target_deviation: float) -> Optional[float]:
        """按当前股票/指数区间收益，反推下一交易日达到目标偏离所需涨幅。"""
        stock_map = {item.get("date"): float(item.get("return")) for item in stock if item.get("date")}
        index_map = {item.get("date"): float(item.get("return")) for item in index if item.get("date")}
        dates = sorted(set(stock_map) & set(index_map))[-actual_window:]
        if len(dates) < actual_window:
            return None
        stock_total = cls._compound(stock_map[day] for day in dates)
        index_total = cls._compound(index_map[day] for day in dates)
        return max(0.0, ((1.0 + (index_total + target_deviation) / 100.0) / (1.0 + stock_total / 100.0) - 1.0) * 100.0)

    @classmethod
    def _vector_metrics(cls, stock: List[Dict[str, Any]], index: List[Dict[str, Any]], rule: Any) -> Dict[str, Any]:
        """从日期化收益率向量计算偏离和 10 日同向异动段次数。

        同一个连续的 3 日异常区间会产生多个重叠滚动窗口，但在规则口径中
        只算一次同向异动；命中的 3 日区间会被消费，下一次从未消费的
        交易日开始扫描。这样连续涨停不会被重叠窗口重复累计成 6 次、7 次。
        """

        deviations = {}
        window_lengths = {}
        for window in (3, 10, 30):
            if window == 3:
                deviations[window] = cls._vector_deviation(stock, index, window)
                window_lengths[window] = window if deviations[window] is not None else None
            else:
                deviations[window], window_lengths[window] = cls._best_vector_deviation(stock, index, window)
        stock_map = {item["date"]: item["return"] for item in stock if item.get("date")}
        index_map = {item["date"]: item["return"] for item in index if item.get("date")}
        common_dates = sorted(set(stock_map) & set(index_map))[-10:]
        if not common_dates:
            common_dates = list(range(min(len(stock), len(index))))[-10:]
            stock_values = cls._vector_numbers(stock[-len(common_dates):])
            index_values = cls._vector_numbers(index[-len(common_dates):])
        else:
            stock_values = [stock_map[item] for item in common_dates]
            index_values = [index_map[item] for item in common_dates]
        up_count = down_count = 0
        end = 2
        while end < len(stock_values):
            deviation = cls._compound(stock_values[end - 2 : end + 1]) - cls._compound(index_values[end - 2 : end + 1])
            direction = 0
            if deviation >= rule.ordinary_deviation:
                direction = 1
            elif deviation <= -rule.ordinary_deviation:
                direction = -1
            if direction == 1:
                up_count += 1
            elif direction == -1:
                down_count += 1
            # 命中的 3 日区间被消费；下一个事件必须从未消费的交易日开始，
            # 避免相邻滚动窗口把同一段连续行情重复计数。
            end += 3 if direction else 1
        same_direction_count = max(up_count, down_count)
        return {
            "deviations": deviations,
            "window_lengths": window_lengths,
            "up": up_count,
            "down": down_count,
            "same": same_direction_count,
        }

    @staticmethod
    def _quote_date_string(quote: Optional[Dict[str, Any]]) -> str:
        value = str((quote or {}).get("updatedAt") or "")
        return value[:10] if len(value) >= 10 else ""

    @classmethod
    def _apply_quote_to_vector(cls, entries: List[Dict[str, Any]], latest_close: Any, quote: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把当前报价转为一个当日收益率，替换或追加最后一个向量元素。"""

        if not quote:
            return list(entries)
        try:
            current = float(quote.get("current"))
            previous_close = float(latest_close)
        except (TypeError, ValueError):
            return list(entries)
        if current <= 0 or previous_close <= 0:
            return list(entries)
        quote_date = cls._quote_date_string(quote)
        if not quote_date:
            return list(entries)
        result = list(entries)
        if result and result[-1].get("date") == quote_date:
            if len(result) >= 2:
                last_return = float(result[-1]["return"])
                previous_close = previous_close / (1.0 + last_return / 100.0)
            result[-1] = {"date": quote_date, "return": (current / previous_close - 1.0) * 100.0}
        elif not result or quote_date > str(result[-1].get("date") or ""):
            result.append({"date": quote_date, "return": (current / previous_close - 1.0) * 100.0})
        return result[-30:]

    def _detail_from_repository(self, stock: Dict[str, Any], calc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        index_code = _index_code_for_stock(stock)
        try:
            index = self._calc_repository.get_index(index_code)
        except Exception as exc:  # noqa: BLE001 - 数据库不可用时由调用方回退
            self._market_update_error = f"MySQL 读取失败：{exc}"
            return None
        if not index:
            return None
        stock_vector = self._vector_entries(calc.get("stock_return_vector"))
        index_vector = self._vector_entries(index.get("index_return_vector"))
        stock_vector, index_vector, suspension = self._trim_vectors_after_suspension(stock_vector, index_vector)
        board = BOARD_BY_MARKET.get(stock["board"], Board.MAIN)
        rule = BOARD_RULES[board]
        quotes = self._realtime.fetch_many([stock["ts_code"], index_code])
        quote = quotes.get(stock["ts_code"].upper())
        index_quote = quotes.get(index_code.upper())
        realtime = bool(quote and index_quote)
        display_quote = quote if realtime else None
        if realtime:
            stock_vector = self._apply_quote_to_vector(stock_vector, calc.get("latest_close"), quote)
            index_vector = self._apply_quote_to_vector(index_vector, index.get("latest_close"), index_quote)
        metrics = self._vector_metrics(stock_vector, index_vector, rule)
        deviations = metrics["deviations"]
        # 展示和状态只使用上涨同向次数；下跌次数仅作为诊断数据保留。
        same_count = metrics["up"]
        severe = SimpleNamespace(
            same_direction_count=same_count,
            same_direction_threshold=rule.severe_same_direction_count,
            up_count=metrics["up"],
            down_count=metrics["down"],
            triggered=(
                deviations.get(10) is not None and rule.severe_10d_threshold.triggered(deviations[10])
            ) or (
                deviations.get(30) is not None and rule.severe_30d_threshold.triggered(deviations[30])
            ) or same_count >= rule.severe_same_direction_count,
        )
        status, status_class = self._status_for_metrics(deviations, metrics["up"], rule)
        current_price = display_quote.get("current") if display_quote else calc.get("latest_close")
        if display_quote and display_quote.get("pctChg") is not None:
            current_change = display_quote.get("pctChg")
        elif stock_vector:
            current_change = stock_vector[-1].get("return")
        else:
            current_change = None
        latest_date = self._quote_date_string(display_quote) or str(calc.get("as_of_trade_date") or "")
        try:
            future_dates = self._future_trade_dates(latest_date.replace("-", ""), 10)
        except Exception:
            future_dates = []
        common_days = len(set(item.get("date") for item in stock_vector if item.get("date")) & set(item.get("date") for item in index_vector if item.get("date")))
        warnings = [
            {"title": "3 日偏离", "value": _pct(deviations[3]), "target": f"阈值 ±{rule.ordinary_deviation:.0f}%" if deviations[3] is not None else "数据不足（需 3 个交易日）", "className": "risk" if deviations[3] is not None and abs(deviations[3]) >= rule.ordinary_deviation else "safe" if deviations[3] is not None else "neutral"},
            {"title": "10 日偏离", "value": _pct(deviations[10]), "target": f"阈值 +{rule.severe_10d_threshold.up:.0f}% / {rule.severe_10d_threshold.down:.0f}%" if deviations[10] is not None else f"数据不足（已有 {common_days} 个交易日，需 10 个）", "className": "risk" if deviations[10] is not None and rule.severe_10d_threshold.triggered(deviations[10]) else "neutral"},
            {"title": "30 日偏离", "value": _pct(deviations[30]), "target": f"阈值 +{rule.severe_30d_threshold.up:.0f}% / {rule.severe_30d_threshold.down:.0f}%" if deviations[30] is not None else f"数据不足（已有 {common_days} 个交易日，需 30 个）", "className": "risk" if deviations[30] is not None and rule.severe_30d_threshold.triggered(deviations[30]) else "neutral"},
            {"title": "10 日同向", "value": "", "up": str(metrics["up"]), "down": str(metrics["down"]), "target": f"阈值 {rule.severe_same_direction_count} 次", "className": "risk" if same_count >= rule.severe_same_direction_count else "safe"},
        ]
        return {
            **stock,
            "status": status,
            "statusClass": status_class,
            "statusIcon": "!" if status_class != "safe" else "✓",
            "currentPrice": f"{float(current_price):.2f}" if current_price is not None else "--",
            "change": _pct(current_change),
            "tradeDate": latest_date,
            "futureTradeDates": future_dates,
            "warnings": warnings,
            "alerts": self._build_alerts(deviations, severe, board),
            "simulationBase": {"stock": self._vector_numbers(stock_vector), "index": self._vector_numbers(index_vector)},
            "calculationInput": {"stock": stock_vector, "index": index_vector, "ordinaryDeviation": rule.ordinary_deviation, "severe10": {"up": rule.severe_10d_threshold.up, "down": rule.severe_10d_threshold.down}, "severe30": {"up": rule.severe_30d_threshold.up, "down": rule.severe_30d_threshold.down}, "sameDirectionThreshold": rule.severe_same_direction_count, "ruleVersion": PREDICTION_RULE_VERSION},
            "simulationThresholds": {
                "10": {"up": rule.severe_10d_threshold.up, "down": rule.severe_10d_threshold.down},
                "30": {"up": rule.severe_30d_threshold.up, "down": rule.severe_30d_threshold.down},
            },
            "dataQuality": {
                "source": "CloudBase MySQL + 实时行情" if realtime else "CloudBase MySQL",
                "tradeDate": latest_date,
                "asOfTradeDate": str(calc.get("as_of_trade_date") or ""),
                "stage": str(calc.get("data_stage") or "formal"),
                "intraday": realtime,
                "realtime": realtime,
                "fallback": not realtime,
                "isComplete": len(stock_vector) >= 30 and len(index_vector) >= 30,
                "suspensionResetDate": suspension.get("resetDate", ""),
                "suspended": suspension.get("suspended", False),
                "quoteUpdatedAt": display_quote.get("updatedAt", "") if display_quote else "",
                "message": (
                    "实时行情暂不可用" if not realtime else ""
                ) if common_days >= 30 else f"复牌后重新累计，当前仅有 {common_days} 个有效交易日，10 日和 30 日偏离暂不可计算",
            },
        }

    def _market_scheduler_loop(self) -> None:
        """按数据阶段更新：15:00 初态，16:00 后正式数据分时重试。

        盘中行情不进入本线程，也不写入数据库。预测池只在初态/正式基础数据
        更新成功后由后台计算；用户刷新只会读取已生成池并临时叠加实时行情。
        """

        self._market_scheduler_stop.wait(0.5)
        while not self._market_scheduler_stop.is_set():
            try:
                if self._calc_repository.available:
                    now = datetime.now(SHANGHAI_TZ)
                    job = self._calc_repository.job() or {}
                    today = now.strftime("%Y%m%d")
                    # 周末、法定节假日和临时休市日不请求行情源，也不刷新基础资料。
                    # 交易日历本身由 TushareClient 缓存，正常情况下每天只请求一次。
                    if not self._is_trade_day(today):
                        self._market_scheduler_stop.wait(60)
                        continue
                    if 15 <= now.hour < 16:
                        # 容器可能在 15:00 后冷启动；只要当日尚未成功生成初态，
                        # 在 15:00-16:00 内补做一次，避免错过整天的初态数据。
                        initial_marker = f"{today}-initial"
                        if self._latest_data_stage_date() != (today, "initial") and self._market_last_formal_attempt != initial_marker:
                            self._market_last_formal_attempt = initial_marker
                            if self.update_initial_data().get("updated"):
                                self._generate_prediction_datasets("initial")
                    elif 16 <= now.hour <= 23:
                        # 16:00-18:00 每 30 分钟，18:00-23:00 每小时；
                        # 正式数据成功后当天不再重复请求，23:00 是最后一次尝试。
                        latest = self.client.latest_trade_date()
                        needs_formal = self._latest_data_stage() != "formal" or self._confirmed_market_trade_date() != latest
                        retry_due = self._formal_retry_due(now)
                        # 冷启动错过整点时，在当前小时的前 30 分钟补做一次；
                        # 23:00 后不再补请求，避免突破当日最后重试边界。
                        catch_up = self._formal_catch_up_due(now)
                        slot_marker = f"{today}-{now.hour:02d}-{now.minute:02d}"
                        catch_up_marker = f"{today}-catchup-{now.hour:02d}"
                        marker = slot_marker if retry_due else catch_up_marker
                        if needs_formal and (retry_due or catch_up) and self._market_last_formal_attempt != marker:
                            self._market_last_formal_attempt = marker
                            result = self.update_market_data(data_stage="formal")
                            if result.get("updated") and result.get("tradeDate") == latest:
                                self._generate_prediction_datasets("formal")
            except Exception as exc:  # noqa: BLE001
                self._market_update_error = str(exc)
            self._market_scheduler_stop.wait(60)

    def _is_trade_day(self, trade_date: str) -> bool:
        """使用交易所日历判断日期是否开市，避免休市日触发行情请求。"""

        value = str(trade_date or "")
        if len(value) != 8 or not value.isdigit():
            return False
        calendar = self.client.trade_cal(value, value)
        return any(
            str(row.get("cal_date") or "") == value and int(row.get("is_open", 0)) == 1
            for row in calendar.to_dict("records")
        )

    @staticmethod
    def _formal_retry_due(now: datetime) -> bool:
        """判断当前分钟是否命中正式数据重试时段。"""

        if now.hour in (16, 17):
            return now.minute in (0, 30)
        if 18 <= now.hour <= 23:
            return now.minute == 0
        return False

    @staticmethod
    def _formal_catch_up_due(now: datetime) -> bool:
        """冷启动补做当前小时已错过的正式重试，23:31 后不再补做。"""

        return 16 <= now.hour <= 23 and 0 < now.minute <= 30

    @staticmethod
    def _return_entry(date_value: str, previous: float, current: float) -> Dict[str, Any]:
        return {"date": str(date_value), "return": (float(current) / float(previous) - 1.0) * 100.0}

    def _append_daily_rows(self, calculations: Dict[str, Dict[str, Any]], daily_frames: Dict[str, Any], latest_dates: List[str]) -> None:
        for trade_date in latest_dates:
            frame = daily_frames.get(trade_date)
            if frame is None or frame.empty:
                continue
            for row in frame.to_dict("records"):
                ts_code = str(row.get("ts_code") or "")
                close = row.get("close")
                if not ts_code or close is None or float(close) <= 0:
                    continue
                item = calculations.get(ts_code)
                if not item:
                    continue
                vector = self._vector_entries(item.get("stock_return_vector"))
                previous_close = item.get("latest_close")
                if previous_close and float(previous_close) > 0:
                    vector = [entry for entry in vector if entry.get("date") != str(trade_date)]
                    vector.append(self._return_entry(trade_date, float(previous_close), float(close)))
                item["stock_return_vector"] = vector[-30:]
                item["latest_close"] = float(close)
                item["as_of_trade_date"] = str(trade_date)

    def update_market_data(self, data_stage: str = "formal") -> Dict[str, Any]:
        """生成或增量更新最小计算数据，不保存原始日线。"""

        if not self._calc_repository.available:
            return {"updated": False, "reason": "MySQL 计算数据仓库未配置"}
        with self._market_update_lock:
            try:
                self._calc_repository.ensure_schema()
                today = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
                if not self._is_trade_day(today):
                    return {"updated": False, "reason": "今天不是交易日", "tradeDate": ""}
                latest = self.client.latest_trade_date()
                try:
                    stock_frame = self.client.stock_basic(force=True)
                except TypeError:
                    stock_frame = self.client.stock_basic()
                stocks = [self._normalise_stock_row(item) for item in stock_frame.to_dict("records")]
                existing = self._calc_repository.all_calculations() or {}
                indexes_existing = self._calc_repository.all_indexes() or {}
                previous_date = max((str(item.get("as_of_trade_date") or "") for item in existing.values()), default="")
                calendar = self.client.trade_cal(
                    (datetime.strptime(latest, "%Y%m%d") - timedelta(days=70)).strftime("%Y%m%d"), latest,
                )
                open_dates = sorted(str(row["cal_date"]) for row in calendar.to_dict("records") if int(row.get("is_open", 0)) == 1)
                bootstrap = not existing
                if bootstrap:
                    needed_dates = open_dates[-31:]
                else:
                    needed_dates = [item for item in open_dates if item > previous_date]
                    # 初态已经写入当前交易日时，正式任务仍必须重新拉取该日
                    # Tushare 收盘数据，不能因日期相同而提前返回。
                    if data_stage == "formal" and self._latest_data_stage() == "initial" and latest not in needed_dates:
                        needed_dates.append(latest)
                    if not needed_dates:
                        # 即使没有新增交易日，也要把当日 stock_basic 替换写入，识别新上市和状态变化。
                        # 但不能因此把尚未入盘的日历日期记成成功日期。
                        data_trade_date = previous_date or latest
                        self._calc_repository.upsert_market(stocks, [], [], data_trade_date, data_stage=data_stage)
                        if data_trade_date != latest:
                            self._calc_repository.record_failure("Tushare 当日收盘数据尚未入盘")
                        self._stocks_cache = (datetime.now(SHANGHAI_TZ), stocks)
                        return {"updated": False, "tradeDate": data_trade_date, "stockCount": len(stocks)}
                daily_frames = {trade_date: self.client.daily(trade_date=trade_date) for trade_date in needed_dates}
                if bootstrap:
                    for item in stocks:
                        rows = []
                        for trade_date in needed_dates:
                            frame = daily_frames.get(trade_date)
                            if frame is None or frame.empty:
                                continue
                            rows.extend(row for row in frame.to_dict("records") if str(row.get("ts_code")) == item["ts_code"] and row.get("close") is not None)
                        rows.sort(key=lambda row: str(row.get("trade_date")))
                        vector = []
                        for previous, current in zip(rows, rows[1:]):
                            vector.append(self._return_entry(current["trade_date"], float(previous["close"]), float(current["close"])))
                        if rows:
                            existing[item["ts_code"]] = {"ts_code": item["ts_code"], "as_of_trade_date": str(rows[-1]["trade_date"]), "latest_close": float(rows[-1]["close"]), "stock_return_vector": vector[-30:]}
                else:
                    self._append_daily_rows(existing, daily_frames, needed_dates)
                    # 新上市股票不在旧计算池中时，只为这些新增代码补取近 30 个交易日价格。
                    history_start = needed_dates[0] if len(needed_dates) >= 30 else open_dates[-31]
                    for stock in stocks:
                        if stock["ts_code"] in existing:
                            continue
                        frame = self.client.daily(ts_code=stock["ts_code"], start_date=history_start, end_date=latest)
                        rows = [] if frame is None or frame.empty else sorted(frame.to_dict("records"), key=lambda row: str(row.get("trade_date")))
                        vector = []
                        for previous, current in zip(rows, rows[1:]):
                            if previous.get("close") and current.get("close"):
                                vector.append(self._return_entry(current["trade_date"], float(previous["close"]), float(current["close"])))
                        if rows and vector:
                            existing[stock["ts_code"]] = {
                                "ts_code": stock["ts_code"], "as_of_trade_date": str(rows[-1]["trade_date"]),
                                "latest_close": float(rows[-1]["close"]), "stock_return_vector": vector[-30:],
                            }
                index_codes = sorted(set(INDEX_BY_MARKET.values()) | set(INDEX_BY_BOARD.values()))
                indexes: List[Dict[str, Any]] = []
                index_latest_flags: List[bool] = []
                for index_code in index_codes:
                    frame = self._index_daily(index_code, needed_dates[0], needed_dates[-1]) if needed_dates else None
                    rows = [] if frame is None or frame.empty else sorted(frame.to_dict("records"), key=lambda row: str(row.get("trade_date")))
                    index_latest_flags.append(any(str(row.get("trade_date")) == latest for row in rows))
                    item = indexes_existing.get(index_code, {"index_code": index_code, "index_name": index_code, "index_return_vector": []})
                    vector = self._vector_entries(item.get("index_return_vector"))
                    previous_close = item.get("latest_close")
                    if bootstrap:
                        vector = []
                        previous_close = None
                    for row in rows:
                        close = row.get("close")
                        if close is None or float(close) <= 0:
                            continue
                        if previous_close and float(previous_close) > 0:
                            vector = [entry for entry in vector if entry.get("date") != str(row["trade_date"])]
                            vector.append(self._return_entry(row["trade_date"], float(previous_close), float(close)))
                        previous_close = float(close)
                        item["as_of_trade_date"] = str(row["trade_date"])
                    if previous_close:
                        item["latest_close"] = previous_close
                    item["index_return_vector"] = vector[-30:]
                    item["indexName"] = item.get("index_name") or index_code
                    indexes.append(item)
                # 正式数据必须同时拿到目标交易日的股票日线和全部对应指数日线。
                # 失败时只更新基础资料，保留已有初态/正式计算行，不能伪造成功。
                if data_stage == "formal" and latest in needed_dates:
                    latest_stock_frame = daily_frames.get(latest)
                    latest_stock_ok = latest_stock_frame is not None and not latest_stock_frame.empty and any(str(row.get("trade_date")) == latest for row in latest_stock_frame.to_dict("records"))
                    latest_index_ok = bool(index_latest_flags) and all(index_latest_flags)
                    if not latest_stock_ok or not latest_index_ok:
                        self._calc_repository.upsert_market(stocks, [], [], previous_date or latest, data_stage="initial" if previous_date == latest else "formal")
                        self._calc_repository.record_failure("Tushare 当日股票或指数收盘数据尚未完整入盘")
                        self._market_update_error = "Tushare 当日股票或指数收盘数据尚未完整入盘"
                        return {"updated": False, "tradeDate": previous_date or latest, "stockCount": len(stocks), "reason": self._market_update_error}
                calculations = []
                index_map = {item["index_code"]: item for item in indexes}
                for stock in stocks:
                    item = existing.get(stock["ts_code"])
                    if not item or not item.get("latest_close"):
                        continue
                    index_item = index_map.get(_index_code_for_stock(stock))
                    if not index_item:
                        continue
                    board = BOARD_BY_MARKET.get(stock["board"], Board.MAIN)
                    metrics = self._vector_metrics(self._vector_entries(item.get("stock_return_vector")), self._vector_entries(index_item.get("index_return_vector")), BOARD_RULES[board])
                    item.update({"deviation3": metrics["deviations"][3], "deviation10": metrics["deviations"][10], "deviation30": metrics["deviations"][30], "sameDirectionUp": metrics["up"], "sameDirectionDown": metrics["down"]})
                    calculations.append(item)
                # 任务日期取实际写入的股票/指数收盘数据日期。交易日历中的
                # 当天可能尚未入盘，不能把它写成已确认日期。
                actual_dates = [str(item.get("as_of_trade_date") or "") for item in calculations + indexes]
                data_trade_date = max((value for value in actual_dates if value), default=previous_date or latest)
                self._calc_repository.upsert_market(stocks, calculations, indexes, data_trade_date, data_stage=data_stage)
                if data_trade_date != latest:
                    self._calc_repository.record_failure("Tushare 当日收盘数据尚未入盘")
                self._stocks_cache = (datetime.now(SHANGHAI_TZ), stocks)
                self._market_update_error = "" if data_trade_date == latest else "Tushare 当日收盘数据尚未入盘"
                return {"updated": True, "tradeDate": data_trade_date, "stockCount": len(stocks), "calculationCount": len(calculations)}
            except Exception as exc:  # noqa: BLE001
                self._market_update_error = str(exc)
                self._calc_repository.record_failure(str(exc))
                raise

    def _latest_data_stage(self) -> str:
        """返回当前计算数据的阶段；空库返回空字符串。"""
        try:
            rows = list((self._calc_repository.all_calculations() or {}).values())
            latest = max((str(item.get("as_of_trade_date") or "") for item in rows), default="")
            stages = [str(item.get("data_stage") or "") for item in rows if str(item.get("as_of_trade_date") or "") == latest]
            return "formal" if "formal" in stages else (stages[0] if stages else "")
        except Exception:
            return ""

    def _latest_data_stage_date(self) -> tuple[str, str]:
        try:
            rows = list((self._calc_repository.all_calculations() or {}).values())
            if not rows:
                return "", ""
            latest = max(str(item.get("as_of_trade_date") or "") for item in rows)
            stage = next((str(item.get("data_stage") or "") for item in rows if str(item.get("as_of_trade_date") or "") == latest), "")
            return latest, stage
        except Exception:
            return "", ""

    def update_initial_data(self) -> Dict[str, Any]:
        """15:00 生成初态数据，只覆盖当前交易日的报价。

        初态与正式数据共用计算基础表；盘中实时报价不调用本方法，也不落库。
        正式数据失败时，初态继续作为最近可用数据保留到下一次初态/正式更新。
        """
        if not self._calc_repository.available:
            return {"updated": False, "reason": "MySQL 计算数据仓库未配置"}
        with self._market_update_lock:
            self._calc_repository.ensure_schema()
            now = datetime.now(SHANGHAI_TZ)
            today = now.strftime("%Y%m%d")
            calendar = self.client.trade_cal((now.date() - timedelta(days=3)).strftime("%Y%m%d"), (now.date() + timedelta(days=3)).strftime("%Y%m%d"))
            if not any(str(row.get("cal_date")) == today and int(row.get("is_open", 0)) == 1 for row in calendar.to_dict("records")):
                return {"updated": False, "reason": "今天不是交易日"}
            stocks = self._stock_rows(force=True)
            existing = self._calc_repository.all_calculations() or {}
            indexes_existing = self._calc_repository.all_indexes() or {}
            index_codes = sorted(set(INDEX_BY_MARKET.values()) | set(INDEX_BY_BOARD.values()))
            quotes = self._realtime.fetch_many([item["ts_code"] for item in stocks] + index_codes)
            indexes: List[Dict[str, Any]] = []
            for code in index_codes:
                item = dict(indexes_existing.get(code) or {"index_code": code, "index_name": code, "index_return_vector": []})
                quote = quotes.get(code.upper())
                if not quote or float(quote.get("current") or 0) <= 0:
                    continue
                quote = dict(quote)
                quote.setdefault("updatedAt", f"{today[:4]}-{today[4:6]}-{today[6:]}T15:00:00")
                vector = self._apply_quote_to_vector(self._vector_entries(item.get("index_return_vector")), item.get("latest_close"), quote)
                item.update({"index_code": code, "index_name": item.get("index_name") or code, "latest_close": float(quote["current"]), "as_of_trade_date": today, "index_return_vector": vector[-30:], "data_stage": "initial"})
                indexes.append(item)
            index_map = {item["index_code"]: item for item in indexes}
            calculations: List[Dict[str, Any]] = []
            for stock in stocks:
                item = existing.get(stock["ts_code"])
                quote = quotes.get(stock["ts_code"].upper())
                index_item = index_map.get(_index_code_for_stock(stock))
                if not item or not quote or not index_item:
                    continue
                quote = dict(quote)
                quote.setdefault("updatedAt", f"{today[:4]}-{today[4:6]}-{today[6:]}T15:00:00")
                vector = self._apply_quote_to_vector(self._vector_entries(item.get("stock_return_vector")), item.get("latest_close"), quote)
                item = dict(item)
                item.update({"ts_code": stock["ts_code"], "latest_close": float(quote["current"]), "as_of_trade_date": today, "stock_return_vector": vector[-30:], "data_stage": "initial"})
                metrics = self._vector_metrics(vector, self._vector_entries(index_item.get("index_return_vector")), BOARD_RULES[BOARD_BY_MARKET.get(stock["board"], Board.MAIN)])
                item.update({"deviation3": metrics["deviations"][3], "deviation10": metrics["deviations"][10], "deviation30": metrics["deviations"][30], "sameDirectionUp": metrics["up"], "sameDirectionDown": metrics["down"]})
                calculations.append(item)
            if not calculations or not indexes:
                return {"updated": False, "reason": "初态行情源未返回完整股票和指数数据"}
            self._calc_repository.upsert_market(stocks, calculations, indexes, today, data_stage="initial")
            self._stocks_cache = (now, stocks)
            return {"updated": True, "tradeDate": today, "dataStage": "initial", "calculationCount": len(calculations)}

    def _generate_prediction_datasets(self, data_stage: str) -> None:
        """基础数据阶段完成后，后台统一生成 today/next_day 两个结果集。"""
        for scope in ("today", "next_day"):
            items = self._compute_prediction_from_repository(scope, use_realtime=False) or []
            dates = [str(item.get("tradeDate") or "") for item in items if item.get("tradeDate")]
            trade_date = max(dates) if dates else self._confirmed_market_trade_date()
            updated_at = datetime.now(SHANGHAI_TZ)
            self._prediction_cache[scope] = (updated_at, items)
            self._prediction_trade_date[scope] = trade_date
            if self._calc_repository.available:
                self._calc_repository.save_prediction(scope, trade_date, items, updated_at, data_stage=data_stage, data_quality="provisional" if data_stage == "initial" else "confirmed", rule_version=PREDICTION_RULE_VERSION)

    def detail(self, ts_code: str) -> Dict[str, Any]:
        """返回单股当前状态、四项预警和模拟计算基础数据。"""

        stock = self._stock(ts_code)
        if self._calc_repository.available:
            try:
                stored = self._calc_repository.get_calc(stock["ts_code"])
                if stored and stored.get("stock_return_vector"):
                    result = self._detail_from_repository(stock, stored)
                    if result:
                        return result
            except Exception as exc:  # noqa: BLE001 - 数据库不可用时回退已有 Tushare 链路
                self._market_update_error = f"MySQL 读取失败：{exc}"
        context = self._market_context()
        latest = context["latest"]
        start = context["start"]
        stock_frame = self._daily(stock["ts_code"], start, latest)
        index_code = _index_code_for_stock(stock)
        index_frame = context["indexFrames"].get(index_code)
        stock_all = self._bars(stock_frame, stock["ts_code"])
        index_all = self._bars(index_frame, index_code)
        stock_all, index_all, suspension = self._trim_bars_after_suspension(stock_all, index_all)
        stock_by_date = {bar.trade_date: bar for bar in stock_all}
        index_by_date = {bar.trade_date: bar for bar in index_all}
        common_dates = sorted(set(stock_by_date) & set(index_by_date))
        stock_bars = [stock_by_date[item] for item in common_dates]
        index_bars = [index_by_date[item] for item in common_dates]
        board = BOARD_BY_MARKET.get(stock["board"], Board.MAIN)
        board_rule = BOARD_RULES[board]
        quotes = self._realtime.fetch_many([stock["ts_code"], index_code])
        quote = quotes.get(stock["ts_code"].upper())
        index_quote = quotes.get(index_code.upper())
        stock_bars, index_bars = self._merge_realtime_bars(stock_bars, index_bars, quote, index_quote)
        common_days = len(stock_bars)
        deviations: Dict[int, Optional[float]] = {}
        for window in (3, 10, 30):
            deviations[window] = None
            if len(stock_bars) >= window + 1:
                deviations[window] = calculate_deviation(stock_bars, index_bars, window).deviation
        severe = detect_severe_abnormal(stock_bars, index_bars, board) if len(stock_bars) == len(index_bars) and len(stock_bars) >= 2 else None
        status, status_class = self._status_for_metrics(
            deviations,
            severe.up_count if severe else 0,
            board_rule,
        )
        latest_row = sorted(stock_frame.to_dict("records"), key=lambda item: str(item["trade_date"]))[-1] if not stock_frame.empty else {}
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
                {"title": "3 日偏离", "value": _pct(deviations[3]), "target": f"阈值 ±{board_rule.ordinary_deviation:.0f}%" if deviations[3] is not None else "数据不足（需 3 个交易日）", "className": "risk" if deviations[3] is not None and abs(deviations[3]) >= board_rule.ordinary_deviation else "safe" if deviations[3] is not None else "neutral"},
                {"title": "10 日偏离", "value": _pct(deviations[10]), "target": f"阈值 +{board_rule.severe_10d_threshold.up:.0f}% / {board_rule.severe_10d_threshold.down:.0f}%" if deviations[10] is not None else f"数据不足（已有 {common_days} 个交易日，需 10 个）", "className": "risk" if deviations[10] is not None and board_rule.severe_10d_threshold.triggered(deviations[10]) else "neutral"},
                {"title": "30 日偏离", "value": _pct(deviations[30]), "target": f"阈值 +{board_rule.severe_30d_threshold.up:.0f}% / {board_rule.severe_30d_threshold.down:.0f}%" if deviations[30] is not None else f"数据不足（已有 {common_days} 个交易日，需 30 个）", "className": "risk" if deviations[30] is not None and board_rule.severe_30d_threshold.triggered(deviations[30]) else "neutral"},
                {"title": "10 日同向", "value": "", "up": str(severe.up_count if severe else 0), "down": str(severe.down_count if severe else 0), "target": f"阈值 {severe.same_direction_threshold if severe else 0} 次", "className": "risk" if severe and severe.same_direction_count else "safe"},
            ],
            "alerts": self._build_alerts(deviations, severe, board),
            "simulationBase": self._simulation_returns(stock_bars, index_bars),
            "calculationInput": {"stock": [{"date": bar.trade_date.isoformat(), "return": value} for bar, value in zip(stock_bars[1:], self._simulation_returns(stock_bars, index_bars)["stock"])], "index": [{"date": bar.trade_date.isoformat(), "return": value} for bar, value in zip(index_bars[1:], self._simulation_returns(stock_bars, index_bars)["index"])], "ordinaryDeviation": board_rule.ordinary_deviation, "severe10": {"up": board_rule.severe_10d_threshold.up, "down": board_rule.severe_10d_threshold.down}, "severe30": {"up": board_rule.severe_30d_threshold.up, "down": board_rule.severe_30d_threshold.down}, "sameDirectionThreshold": board_rule.severe_same_direction_count, "ruleVersion": PREDICTION_RULE_VERSION},
            "simulationThresholds": {
                "10": {"up": board_rule.severe_10d_threshold.up, "down": board_rule.severe_10d_threshold.down},
                "30": {"up": board_rule.severe_30d_threshold.up, "down": board_rule.severe_30d_threshold.down},
            },
            "dataQuality": {
                "source": "tushare+腾讯行情" if quote and index_quote else "tushare",
                "tradeDate": latest,
                "stage": "realtime" if quote and index_quote else "formal",
                "isComplete": len(stock_bars) >= 31,
                "intraday": bool(quote and index_quote),
                "realtime": bool(quote and index_quote),
                "fallback": not bool(quote and index_quote),
                "quoteUpdatedAt": quote.get("updatedAt") if quote else "",
                "suspensionResetDate": suspension.get("resetDate", ""),
                "suspended": suspension.get("suspended", False),
                "message": ("实时行情暂不可用" if not (quote and index_quote) else "") if common_days >= 30 else f"复牌后重新累计，当前仅有 {common_days} 个有效交易日，10 日和 30 日偏离暂不可计算",
            },
        }

    def _compute_prediction_from_repository(self, scope: str, use_realtime: bool = False) -> Optional[List[Dict[str, Any]]]:
        """从股票/指数收益率数据集计算候选，绝不读取原始日线。"""

        calculations = self._calc_repository.all_calculations()
        indexes = self._calc_repository.all_indexes()
        if calculations is None or indexes is None or not calculations or not indexes:
            return None
        stocks = {item["ts_code"]: item for item in self._stock_rows()}
        candidates: List[Dict[str, Any]] = []
        for ts_code, calc in calculations.items():
            stock = stocks.get(ts_code)
            if not stock:
                continue
            index_code = _index_code_for_stock(stock)
            index = indexes.get(index_code)
            if not index:
                continue
            board = BOARD_BY_MARKET.get(stock["board"], Board.MAIN)
            rule = BOARD_RULES[board]
            stock_vector = self._vector_entries(calc.get("stock_return_vector"))
            index_vector = self._vector_entries(index.get("index_return_vector"))
            stock_vector, index_vector, suspension = self._trim_vectors_after_suspension(stock_vector, index_vector)
            # 停牌尾部没有当前有效价格，不能把停牌前结果伪装成次日候选。
            # 复牌后的向量已经在上面截断，从复牌首个交易日重新累计。
            if suspension.get("suspended"):
                continue
            metrics = self._vector_metrics(stock_vector, index_vector, rule)
            # 当日/次日预测都检查 10 日和 30 日严重异动线，但预测只保留
            # 上涨方向。预测页回答的是“下一交易日上涨后是否可能触线”，
            # 不是把已经发生的下跌异动重新列一遍。
            options = []
            for candidate_window, candidate_threshold in (
                (10, rule.severe_10d_threshold),
                (30, rule.severe_30d_threshold),
            ):
                candidate_deviation = metrics["deviations"].get(candidate_window)
                actual_window = metrics.get("window_lengths", {}).get(candidate_window) or candidate_window
                # 下跌方向不进入预测池；不能用 abs() 把负值变成“距离很近”。
                if candidate_deviation is None or candidate_deviation <= 0:
                    continue
                candidate_distance = self._required_up_percent(stock_vector, index_vector, actual_window, candidate_threshold.up)
                if candidate_distance is None:
                    continue
                # 旧逻辑对所有板块固定放宽 20%，会把主板下一交易日
                # 不可能触发的股票也放进来。使用所属板块单日涨幅上限。
                possible_one_day = float(rule.limit_price_ratio or 10.0)
                if scope == "today":
                    # 当日数据已经收盘时，只展示确实达到上涨阈值的记录。
                    if candidate_deviation >= candidate_threshold.up:
                        options.append((0.0, candidate_window, candidate_deviation, candidate_threshold, "deviation", actual_window))
                elif 0 < candidate_distance <= possible_one_day:
                    # 次日只展示下一交易日仍有可能上涨触线的记录。
                    options.append((candidate_distance, candidate_window, candidate_deviation, candidate_threshold, "deviation", actual_window))
            # 同向条件只统计上涨次数；下跌同向不进入预测池。
            up_direction = metrics["up"]
            same_deviation = metrics["deviations"].get(10)
            if up_direction >= rule.severe_same_direction_count and same_deviation is not None and same_deviation > 0:
                same_actual_window = metrics.get("window_lengths", {}).get(10) or 10
                same_distance = self._required_up_percent(stock_vector, index_vector, same_actual_window, rule.severe_10d_threshold.up)
                if same_distance is None:
                    continue
                # 当日已经达到同向次数即可入选；次日仍只展示一个交易日内
                # 可能触线的记录，不能把“已达到”当成新的次日候选。
                if (scope == "today" and same_distance >= 0) or (scope == "next_day" and 0 < same_distance <= float(rule.limit_price_ratio or 10.0)):
                    options.append((same_distance, 10, same_deviation, rule.severe_10d_threshold, "same_direction", metrics.get("window_lengths", {}).get(10) or 10))
            if not options:
                continue

            # 一只股票的 10 日、30 日条件分别展示；同向条件只并入 10 日卡，
            # 不再用 min(options) 丢掉同时满足的另一条规则。
            selected_by_window: Dict[int, tuple] = {}
            for option in options:
                distance, window, deviation, threshold, trigger_kind, actual_window = option
                current = selected_by_window.get(window)
                if current is None or (trigger_kind == "same_direction" and current[4] != "same_direction"):
                    selected_by_window[window] = option
            for window in sorted(selected_by_window):
                distance, window, deviation, threshold, trigger_kind, actual_window = selected_by_window[window]
                if deviation is None:
                    continue
                candidate = {
                    **stock,
                    "predictionKey": f"{ts_code}-{window}d",
                    "predictionWindow": window,
                    "scope": "当日" if scope == "today" else "次日",
                    "currentPrice": f"{float(calc.get('latest_close')):.2f}" if calc.get("latest_close") is not None else "--",
                    "change": _pct(stock_vector[-1].get("return") if stock_vector else None),
                    "trigger": (
                        f"同向上涨达到 {up_direction} 次（要求 {rule.severe_same_direction_count} 次）"
                        if trigger_kind == "same_direction" else
                        (f"上涨 ≥ {max(0.01, distance):.2f}%" if distance > 0 else "已达到阈值")
                    ),
                    "deviation": f"{actual_window}日 {_pct(deviation)}",
                    "rule": (
                        f"连续10个交易日内偏离值达到 +{threshold.up:.0f}% 或同向上涨达到 {rule.severe_same_direction_count} 次"
                        if trigger_kind == "same_direction" else
                        f"连续{window}个交易日内偏离值达到 +{threshold.up:.0f}%"
                    ),
                    "tradeDate": str(calc.get("as_of_trade_date") or ""),
                    "suspensionResetDate": suspension.get("resetDate", ""),
                }
                candidate["_stockVector"] = stock_vector
                candidate["_indexVector"] = index_vector
                candidate["_indexCode"] = index_code
                candidate["_indexLatestClose"] = index.get("latest_close")
                candidate["_deviation"] = deviation
                candidate["_window"] = actual_window
                candidate["_rule_window"] = window
                candidate["_threshold"] = threshold
                candidates.append(candidate)

        if use_realtime and candidates:
            codes = []
            for item in candidates:
                codes.extend((item["ts_code"], item["_indexCode"]))
            quotes = self._realtime.fetch_many(codes)
            updated: List[Dict[str, Any]] = []
            for item in candidates:
                quote = quotes.get(item["ts_code"].upper())
                index_quote = quotes.get(item["_indexCode"].upper())
                stock_vector = self._apply_quote_to_vector(item["_stockVector"], item.get("currentPrice"), quote)
                index_vector = self._apply_quote_to_vector(item["_indexVector"], item.get("_indexLatestClose"), index_quote)
                # 指数没有把 latest_close 放入预测对象时，只有在同时具备两边报价
                # 才修正；缺一边就沿用同一收盘口径，不能混合时点数据。
                if quote and index_quote:
                    rule = item["_threshold"]
                    stock_map = {x.get("date"): x.get("return") for x in stock_vector if x.get("date")}
                    index_map = {x.get("date"): x.get("return") for x in index_vector if x.get("date")}
                    deviation, actual_window = self._best_vector_deviation(stock_vector, index_vector, item.get("_rule_window", item["_window"]))
                    if deviation is not None:
                        # 实时修正后仍只保留上涨方向；不能把盘中下跌
                        # 的候选转换成下跌预测。
                        if deviation <= 0:
                            continue
                        target = rule.up
                        distance = self._required_up_percent(stock_vector, index_vector, actual_window or item["_window"], target) or 0.0
                        item["_deviation"] = deviation
                        item["_window"] = actual_window or item["_window"]
                        item["deviation"] = f"{item['_window']}日 {_pct(deviation)}"
                        item["trigger"] = f"上涨 ≥ {max(0.01, distance):.2f}%" if distance > 0 else "已达到阈值"
                    item["currentPrice"] = f"{float(quote.get('current')):.2f}"
                    item["change"] = _pct(quote.get("pctChg"))
                    item["quoteUpdatedAt"] = quote.get("updatedAt", "")
                    item["quoteSource"] = quote.get("source", "腾讯行情")
                updated.append(item)
            candidates = updated
        for item in candidates:
            for key in ("_stockVector", "_indexVector", "_indexCode", "_indexLatestClose", "_deviation", "_window", "_rule_window", "_threshold"):
                item.pop(key, None)
        candidates.sort(key=lambda item: abs(float(item.get("deviation", "0").split()[-1].replace("%", ""))) if item.get("deviation") else 999, reverse=True)
        return candidates[:100]

    def _compute_prediction_from_daily(self, scope: str) -> List[Dict[str, Any]]:
        """初始化数据集前的兼容回退；仅作为冷启动临时路径。"""

        latest = self.client.latest_trade_date()
        latest_day = datetime.strptime(latest, "%Y%m%d")
        start = (latest_day - timedelta(days=70)).strftime("%Y%m%d")
        stocks = self._stock_rows()
        # Tushare daily 支持 trade_date 全市场查询；按日期拉取并按股票聚合。
        trade_dates = self.client.trade_cal(start, latest)
        # 30 日规则只需要最近 31 个交易日，减少一次全市场日线请求。
        open_dates = [str(item["cal_date"]) for item in trade_dates.to_dict("records") if int(item["is_open"]) == 1][-31:]
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
            index_code = _index_code_for_stock(stock)
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
            board_rule = BOARD_RULES[threshold]
            if window == 30:
                target_up = board_rule.severe_30d_threshold.up
                target_down = board_rule.severe_30d_threshold.down
            elif window == 10:
                target_up = board_rule.severe_10d_threshold.up
                target_down = board_rule.severe_10d_threshold.down
            else:
                target_up = board_rule.ordinary_deviation
                target_down = -board_rule.ordinary_deviation
            # 预测只保留上涨方向，并按板块单日涨幅上限过滤；旧的固定
            # 20% 放宽会让主板不可能在下一交易日触线的股票进入列表。
            if deviation <= 0:
                continue
            target = target_up
            distance = max(0.0, target - deviation)
            if scope == "today":
                if deviation < target_up:
                    continue
            elif not (0 < distance <= float(board_rule.limit_price_ratio or 10.0)):
                continue
            last = sorted(rows, key=lambda item: str(item["trade_date"]))[-1]
            result.append({
                **stock,
                "scope": "当日" if scope == "today" else "次日",
                "currentPrice": f"{float(last.get('close')):.2f}" if last.get("close") is not None else "--",
                "change": _pct(last.get("pct_chg")),
                "trigger": f"上涨 ≥ {max(0.01, distance):.2f}%" if distance > 0 else "已达到阈值",
                "deviation": f"{window}日 {_pct(deviation)}",
                "rule": f"连续{window}个交易日内偏离值达到 +{target_up:.0f}%",
                "tradeDate": latest,
            })
        if self._prediction_state()["phase"] == "intraday":
            quotes = self._realtime.fetch_many(item["ts_code"] for item in result)
            for item in result:
                quote = quotes.get(item["ts_code"])
                if quote:
                    item["currentPrice"] = f"{float(quote.get('current')):.2f}"
                    item["change"] = _pct(quote["pctChg"])
                    item["quoteUpdatedAt"] = quote.get("updatedAt", "")
                    item["quoteSource"] = quote.get("source", "腾讯行情")
        result.sort(key=lambda item: abs(float(item["deviation"].split()[-1].replace("日", "").replace("%", ""))) if item.get("deviation") else 999, reverse=True)
        output = result[:100]
        # 更新时间以扫描完成为准，避免把后台计算耗时误算进快照年龄。
        self._prediction_cache[scope] = (datetime.now(SHANGHAI_TZ), output)
        self._prediction_trade_date[scope] = latest
        return output

    def _prediction_state(self) -> Dict[str, Any]:
        """按上海交易日和时钟决定预测页的显示状态。

        00:00-09:00 属于盘前；09:00-15:00 属于盘中；15:00 后等待
        Tushare 或临时收盘数据。状态只控制展示，不会在 GET 请求中采集数据。
        """

        now = datetime.now(SHANGHAI_TZ)
        if self._prediction_state_cache and (now - self._prediction_state_cache[0]).total_seconds() < 30:
            return self._prediction_state_cache[1]
        today = now.date()
        calendar = self.client.trade_cal(
            (today - timedelta(days=10)).strftime("%Y%m%d"),
            (today + timedelta(days=15)).strftime("%Y%m%d"),
        )
        opened = sorted(str(row["cal_date"]) for row in calendar.to_dict("records") if int(row.get("is_open", 0)) == 1)
        today_value = today.strftime("%Y%m%d")
        is_trade_day = today_value in opened
        # 非交易日把“当前 T”定义为下一交易日，避免周末/节假日把
        # 周一错误地展示成“次日”或复用周五之后的旧日期。
        target_trade_date = today_value if is_trade_day else min((item for item in opened if item > today_value), default="")
        previous = max((item for item in opened if item < target_trade_date), default=self.client.latest_trade_date())
        following = min((item for item in opened if item > target_trade_date), default="")
        if not is_trade_day or now.hour < 9:
            phase = "pre_open"
        elif now.hour < 15:
            phase = "intraday"
        else:
            # 交易日历只说明今天应当交易；是否已经入盘必须看计算数据集
            # 的真实 as_of_trade_date，不能用 calendar 的日期代替。
            phase = "post_close_confirmed" if self._confirmed_market_trade_date() == today_value else "post_close_pending"
        # targetTradeDate 是用户当前所处交易日；非交易日使用下一交易日，
        # tradeDate 仍由预测数据集自身的 as-of 日期决定，避免把未入盘价格
        # 冒充正式收盘价。
        state = {
            "tradeDate": target_trade_date,
            "targetTradeDate": target_trade_date,
            "previousTradeDate": previous,
            "nextTradeDate": following,
            "phase": phase,
            # 09:00 前、盘中以及收盘待确认阶段，当前页都展示“下一交易日
            # 预测”已经生成的本交易日候选。只有正式收盘数据确认后，才把
            # 当前页切回今天的正式结果，并开放真正的下一交易日按钮。
            "todaySourceScope": "today" if phase == "post_close_confirmed" else "next_day",
        }
        self._prediction_state_cache = (now, state)
        return state

    def _confirmed_market_trade_date(self) -> str:
        """返回已经落入基础数据集的最近交易日，不把日历日期当收盘日期。"""

        if self._calc_repository.available:
            try:
                dates = []
                for item in (self._calc_repository.all_calculations() or {}).values():
                    value = str(item.get("as_of_trade_date") or "")
                    if value and str(item.get("data_stage") or "formal") == "formal":
                        dates.append(value)
                for item in (self._calc_repository.all_indexes() or {}).values():
                    value = str(item.get("as_of_trade_date") or "")
                    if value and str(item.get("data_stage") or "formal") == "formal":
                        dates.append(value)
                if dates:
                    return max(dates)
                # 只有初态数据时不能把 T 误报为 Tushare 已入盘。
                if self._calc_repository.all_calculations():
                    return ""
            except Exception as exc:  # noqa: BLE001 - 失败时保留 Tushare 兼容路径
                self._market_update_error = f"行情确认日期读取失败：{exc}"
        return self.client.latest_trade_date()

    @staticmethod
    def _format_prediction_datetime(value: Any) -> str:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=SHANGHAI_TZ)
            return value.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
        text = str(value or "")
        return text[:16].replace("T", " ") if text else ""

    @staticmethod
    def _age_seconds(value: Any) -> Optional[int]:
        """把持久化更新时间转成健康检查可读的数据年龄。"""

        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value if value.tzinfo else value.replace(tzinfo=SHANGHAI_TZ)
        else:
            text = str(value).strip().replace("T", " ")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
        return max(0, int((datetime.now(SHANGHAI_TZ) - parsed.astimezone(SHANGHAI_TZ)).total_seconds()))

    def prediction_health(self, today_is_trade_day: Optional[bool] = None) -> Dict[str, Any]:
        """返回两个预测数据集的更新时间、年龄和错误状态。"""

        result: Dict[str, Any] = {}
        for scope in ("today", "next_day"):
            record: Optional[Dict[str, Any]] = None
            if self._calc_repository.available:
                try:
                    record = self._calc_repository.get_prediction(scope)
                except Exception as exc:  # noqa: BLE001 - 健康接口不能影响业务接口
                    result[scope] = {"available": False, "error": str(exc)[:160]}
                    continue
            cached = self._prediction_cache.get(scope)
            updated = (record or {}).get("updated_at") or (cached[0] if cached else None)
            items = (record or {}).get("items")
            if items is None and cached:
                items = cached[1]
            age_seconds = self._age_seconds(updated)
            result[scope] = {
                "available": bool(record or cached),
                "tradeDate": str((record or {}).get("trade_date") or self._prediction_trade_date.get(scope, "")),
                "updatedAt": self._format_prediction_datetime(updated),
                "ageSeconds": age_seconds,
                # 节假日允许沿用最近成功数据，不把自然日年龄误报成故障。
                "stale": bool(age_seconds is None or (age_seconds > 36 * 3600 and today_is_trade_day is not False)),
                "itemCount": len(items or []),
                "dataStage": str((record or {}).get("data_stage") or "formal"),
                "dataQuality": str((record or {}).get("data_quality") or "confirmed"),
                "ruleVersion": str((record or {}).get("rule_version") or PREDICTION_RULE_VERSION),
                "lastError": str((record or {}).get("last_error") or self._prediction_last_error.get(scope, ""))[:160],
            }
        return result

    def predictions(self, scope: str, force: bool = False) -> Dict[str, Any]:
        if scope not in ("today", "next_day"):
            raise ValueError("scope 必须是 today 或 next_day")
        now = datetime.now(SHANGHAI_TZ)
        state = self._prediction_state()
        source_scope = state["todaySourceScope"] if scope == "today" else "next_day"
        persistent = None
        if self._calc_repository.available:
            try:
                persistent = self._calc_repository.get_prediction(source_scope)
            except Exception as exc:
                self._prediction_last_error[source_scope] = str(exc)
        if persistent:
            updated = persistent.get("updated_at")
            updated_dt = updated.replace(tzinfo=SHANGHAI_TZ) if isinstance(updated, datetime) and updated.tzinfo is None else updated
            if isinstance(updated_dt, datetime):
                self._prediction_cache[source_scope] = (updated_dt, persistent.get("items") or [])
            persistent_item_dates = [str(item.get("tradeDate") or "") for item in (persistent.get("items") or []) if item.get("tradeDate")]
            # 数据集日期以实际候选的计算日期为准，避免旧版本错误写入自然日。
            self._prediction_trade_date[source_scope] = max(persistent_item_dates) if persistent_item_dates else str(persistent.get("trade_date") or "")
            if persistent.get("last_error"):
                self._prediction_last_error[source_scope] = str(persistent.get("last_error"))
        # 盘中实时结果只存在于当前进程的短生命周期内，绝不写入 prediction_cache。
        ephemeral = self._prediction_ephemeral.get(source_scope)
        if ephemeral and (now - ephemeral[0]).total_seconds() <= 90:
            self._prediction_cache[source_scope] = ephemeral
        next_persistent = persistent if source_scope == "next_day" else None
        next_cached = self._prediction_cache.get("next_day")
        if next_persistent is None and self._calc_repository.available:
            try:
                next_persistent = self._calc_repository.get_prediction("next_day")
                if next_persistent:
                    next_updated = next_persistent.get("updated_at")
                    next_dt = next_updated.replace(tzinfo=SHANGHAI_TZ) if isinstance(next_updated, datetime) and next_updated.tzinfo is None else next_updated
                    if isinstance(next_dt, datetime):
                        next_cached = (next_dt, next_persistent.get("items") or [])
                    next_item_dates = [str(item.get("tradeDate") or "") for item in (next_persistent.get("items") or []) if item.get("tradeDate")]
                    self._prediction_trade_date["next_day"] = max(next_item_dates) if next_item_dates else str(next_persistent.get("trade_date") or "")
                    self._prediction_cache["next_day"] = next_cached or (now, next_persistent.get("items") or [])
                    next_cached = self._prediction_cache["next_day"]
            except Exception as exc:
                self._prediction_last_error["next_day"] = str(exc)
        cached = self._prediction_cache.get(source_scope)
        items = cached[1] if cached else []
        trade_date = self._prediction_trade_date.get(source_scope, "")
        next_trade_date = self._prediction_trade_date.get("next_day", "")
        # 数据集即使候选列表为空也代表已经完成计算，不能用 items 长度判断
        # 次日按钮是否开放；同时必须有实际数据日期，避免把刷新租约空行当成结果。
        next_trade_available = bool(next_cached and next_trade_date) and state["phase"] == "post_close_confirmed" and next_trade_date == state["tradeDate"]
        if scope == "next_day" and state["phase"] != "post_close_confirmed":
            items = []
        # GET 只读已有数据；后台租约状态仅用于展示，不会在这里启动刷新。
        # 旧表中的 refreshing_until 仅为历史兼容字段；当前后台更新不由用户请求触发。
        refreshing = False
        updated_at = self._format_prediction_datetime(cached[0]) if cached else ""
        # 旧的预测数据可能保留 quoteSource；只有当前确实处于盘中，且本次
        # 结果同时拿到实时行情时，才向前端宣称使用实时源。
        has_realtime = state["phase"] == "intraday" and any(item.get("quoteSource") for item in items)
        stored_stage = str((persistent or {}).get("data_stage") or "formal")
        ephemeral_active = bool(ephemeral and cached is ephemeral and (now - ephemeral[0]).total_seconds() <= 90)
        quality = "realtime" if ephemeral_active else str((persistent or {}).get("data_quality") or ("provisional" if stored_stage == "initial" else "confirmed"))
        return {
            "scope": scope,
            "items": items,
            "tradeDate": trade_date,
            "targetTradeDate": state["targetTradeDate"],
            "previousTradeDate": state["previousTradeDate"],
            "nextTradeDate": state["nextTradeDate"],
            "phase": state["phase"],
            "todaySourceScope": state["todaySourceScope"],
            "nextDayAvailable": next_trade_available,
            "nextDayReason": "等待当日正式收盘数据" if not next_trade_available else "",
            "updatedAt": updated_at,
            "dataStage": "realtime" if ephemeral_active else stored_stage,
            "refreshing": refreshing,
            "error": self._prediction_last_error.get(source_scope, ""),
            "dataQuality": {
                "source": "腾讯/新浪/东方财富" if has_realtime else ("Tushare" if stored_stage == "formal" else "腾讯/新浪/东方财富"),
                "intraday": ephemeral_active,
                "quality": quality,
                "stage": "realtime" if ephemeral_active else stored_stage,
                "message": "实时行情可能存在延迟" if ephemeral_active else ("初态数据，待 Tushare 正式数据确认" if stored_stage == "initial" else "基于最近已确认收盘数据"),
            },
        }

    def _refresh_existing_prediction_scope(self, source_scope: str) -> List[Dict[str, Any]]:
        """只刷新已有预测池，不重新扫描全市场，也不保存盘中结果。"""
        persistent = self._calc_repository.get_prediction(source_scope) if self._calc_repository.available else None
        base_items = list((persistent or {}).get("items") or self._prediction_cache.get(source_scope, (datetime.now(SHANGHAI_TZ), []))[1])
        if not base_items:
            return []
        calculations = self._calc_repository.all_calculations() if self._calc_repository.available else {}
        indexes = self._calc_repository.all_indexes() if self._calc_repository.available else {}
        stocks = {item["ts_code"]: item for item in self._stock_rows()}
        codes: List[str] = []
        for item in base_items:
            stock = stocks.get(item.get("ts_code"))
            if stock:
                codes.extend((item["ts_code"], _index_code_for_stock(stock)))
        quotes = self._realtime.fetch_many(codes)
        output: List[Dict[str, Any]] = []
        for original in base_items:
            item = dict(original)
            stock = stocks.get(item.get("ts_code"))
            calc = (calculations or {}).get(item.get("ts_code"))
            index_code = _index_code_for_stock(stock) if stock else ""
            index = (indexes or {}).get(index_code)
            quote = quotes.get(str(item.get("ts_code") or "").upper())
            index_quote = quotes.get(index_code.upper()) if index_code else None
            if quote:
                item["currentPrice"] = f"{float(quote.get('current')):.2f}"
                item["change"] = _pct(quote.get("pctChg"))
                item["quoteUpdatedAt"] = quote.get("updatedAt", "")
                item["quoteSource"] = quote.get("source", "腾讯行情")
            # 只有股票和对应指数同时成功，才临时修正偏离值，避免混合时点。
            if quote and index_quote and calc and index:
                stock_vector = self._apply_quote_to_vector(self._vector_entries(calc.get("stock_return_vector")), calc.get("latest_close"), quote)
                index_vector = self._apply_quote_to_vector(self._vector_entries(index.get("index_return_vector")), index.get("latest_close"), index_quote)
                stock_vector, index_vector, suspension = self._trim_vectors_after_suspension(stock_vector, index_vector)
                if suspension.get("suspended"):
                    continue
                match = re.match(r"(\d+)日", str(item.get("deviation") or ""))
                rule_window = int(item.get("predictionWindow") or (match.group(1) if match else 10))
                rule = BOARD_RULES[BOARD_BY_MARKET.get(stock.get("board"), Board.MAIN)]
                metrics = self._vector_metrics(stock_vector, index_vector, rule)
                deviation = metrics.get("deviations", {}).get(rule_window)
                actual_window = metrics.get("window_lengths", {}).get(rule_window) or rule_window
                # 盘中只修正已经生成的候选，不重新扫描全市场；但修正必须使用
                # 与后台生成、单股详情相同的有效窗口和复牌重置逻辑。
                if deviation is not None and deviation > 0:
                    item["deviation"] = f"{actual_window}日 {_pct(deviation)}"
                    threshold = rule.severe_30d_threshold if rule_window == 30 else rule.severe_10d_threshold
                    distance = self._required_up_percent(stock_vector, index_vector, actual_window, threshold.up)
                    if distance is not None:
                        item["trigger"] = f"上涨 ≥ {distance:.2f}%" if distance > 0 else "已达到阈值"
                        item["rule"] = f"连续{rule_window}个交易日内偏离值达到 +{threshold.up:.0f}%"
            output.append(item)
        return output

    def refresh_predictions(self, requested_scope: str = "today") -> Dict[str, Any]:
        """处理预测页刷新按钮，不让 GET 或 Tab 切换触发采集。

        盘前刷新当前日数据；盘中刷新页面当前展示的上一交易日次日结果，
        只对历史筛选出的候选股票请求实时行情。收盘后按钮只读取后台已准备
        的数据集，避免一次点击再次扫描全市场。
        """

        state = self._prediction_state()
        # 只有盘中刷新实时行情，而且只请求已生成的当日预测池；其他时段
        # 直接读取后台结果集。多个用户同时刷新时共享同一个 in-flight 请求。
        if state["phase"] == "intraday":
            source_scope = "next_day"
            with self._intraday_refresh_lock:
                future = self._intraday_refresh_futures.get(source_scope)
                if future is None or future.done():
                    future = self._prediction_executor.submit(self._refresh_existing_prediction_scope, source_scope)
                    self._intraday_refresh_futures[source_scope] = future
            try:
                items = future.result(timeout=10)
                updated_at = datetime.now(SHANGHAI_TZ)
                self._prediction_ephemeral[source_scope] = (updated_at, items)
            except Exception as exc:
                self._prediction_last_error[source_scope] = str(exc)
        return self.predictions(requested_scope)
