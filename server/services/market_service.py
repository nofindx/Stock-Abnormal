"""行情业务层：基础资料、单票计算、重点监控和异动预测。

所有对外字段都在这里转换成小程序需要的中文展示结构；Tushare 字段不会直接泄漏到前端。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
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


INDEX_BY_MARKET = {"SSE": "000001.SH", "SZSE": "399107.SZ", "BSE": "899050.BJ"}
INDEX_BY_BOARD = {"创业板": "399102.SZ", "科创板": "000688.SH", "北交所": "899050.BJ"}
BOARD_BY_MARKET = {"主板": Board.MAIN, "创业板": Board.CHINEXT, "科创板": Board.STAR, "北交所": Board.BSE}
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone(timedelta(hours=8))
# 停牌/复牌重置、上涨同向计数和预测阶段切换属于同一套规则口径。
# 版本变化会触发服务启动时从已确认收盘行情重建计算基础数据与预测集。
PREDICTION_RULE_VERSION = "2026-10-unified-max-window-simulation-v31-rule-reset"


class CalculationDataUnavailable(RuntimeError):
    """单股正式计算依赖完整的 MySQL 收益率基线。"""

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

    # GBK 区位表无法判断多音字；股票简称中“银行”是高频固定词组，
    # ``银行`` 的拼音首字母是 ``YH``，不能被区位表误判为 ``YX``。
    text = str(value or "")
    bank_suffix = "银行"
    suffix_index = text.find(bank_suffix)
    if suffix_index >= 0:
        return _name_initials(text[:suffix_index]) + "YH" + _name_initials(text[suffix_index + len(bank_suffix):])

    result: List[str] = []
    for char in text:
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


def _price_limit_enabled(item: Dict[str, Any], board: str, exchange: str) -> Optional[bool]:
    """归一化价格限制标记。

    Tushare ``stock_basic`` 没有稳定的静态 price-limit 字段；对已上市的四个
    A 股板块按交易所板块规则填充 ``True``，首日资格仍由 list_date 单独判断。
    显式上游值优先，未知交易所保持 None 并按保守策略排除。
    """

    explicit = item.get("priceLimitEnabled", item.get("price_limit_enabled"))
    if explicit is not None:
        return bool(explicit)
    if str(item.get("list_status") or "L") != "L":
        return False
    if str(exchange or "") in {"SSE", "SZSE", "BSE"} and board in BOARD_BY_MARKET:
        return True
    return None


def _index_code_for_stock(stock: Dict[str, Any]) -> str:
    """按股票板块选择对应指数；主板再区分沪深市场。"""

    board = str(stock.get("board") or "主板")
    if board in INDEX_BY_BOARD:
        return INDEX_BY_BOARD[board]
    return INDEX_BY_MARKET.get(str(stock.get("market") or ""), "000001.SH")


def _subsequence_positions(query: str, candidate: str) -> Optional[List[int]]:
    """返回 query 在 candidate 中按顺序匹配的位置；不匹配返回 None。"""

    positions: List[int] = []
    start = 0
    for char in query:
        position = candidate.find(char, start)
        if position < 0:
            return None
        positions.append(position)
        start = position + 1
    return positions


def _market_label(value: Any) -> str:
    return {"SSE": "沪市", "SZSE": "深市", "BSE": "北交所"}.get(str(value or "").upper(), str(value or ""))


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
        self._prediction_generation_stats: Dict[str, Dict[str, int]] = {}
        self._clock = lambda: datetime.now(SHANGHAI_TZ)
        self._prediction_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prediction")
        self._intraday_refresh_futures: Dict[str, Any] = {}
        self._intraday_refresh_lock = Lock()
        self._prediction_rebuild_lock = Lock()
        self._realtime = RealtimeQuoteClient()
        self._market_scheduler_stop = Event()
        self._market_scheduler: Optional[Thread] = None
        self._stock_basic_sync_thread: Optional[Thread] = None
        self._market_update_lock = Lock()
        self._market_last_formal_attempt = ""
        self._market_update_error = ""
        self._monitor_provider = None
        # 重点监控由 OfficialMonitorService 公告快照链路独立负责。

    def set_monitor_provider(self, provider: Any) -> None:
        """注入重点监控当前数据读取器，生成次日数据集时排除同规则状态。"""
        self._monitor_provider = provider

    def _monitored_rule_codes(self) -> Dict[str, set]:
        """返回当前重点监控中各规则对应的股票代码。

        `__severe_monitor__` 是产品层资格标记：股票进入 10 日或 30 日
        严重异动监管期后，预测页才允许额外展示普通/同向短窗口卡。
        """
        if not self._monitor_provider:
            return {}
        try:
            snapshot = self._monitor_provider() or {}
            result: Dict[str, set] = {}
            today = self._now().strftime("%Y-%m-%d")
            for item in snapshot.get("items", []):
                dates = item.get("monitorDates")
                if isinstance(dates, list) and dates:
                    if today not in {str(value)[:10] for value in dates}:
                        continue
                elif item.get("monitorEndDate") and str(item.get("monitorEndDate"))[:10] < today:
                    continue
                tone = str(item.get("riskTone") or "")
                rule_keys = {
                    "severe-10d": {"severe_10d"},
                    "severe-30d": {"severe_30d"},
                }.get(tone, set())
                if tone in {"severe-10d", "severe-30d"}:
                    rule_keys = set(rule_keys) | {"__severe_monitor__"}
                for rule_key in rule_keys:
                    symbol = str(item.get("symbol") or item.get("ts_code") or "")
                    result.setdefault(rule_key, set()).update({symbol, symbol.split(".")[0]})
            return result
        except Exception:
            return {}

    @staticmethod
    def _prediction_code_tokens(item: Dict[str, Any]) -> set:
        ts_code = str(item.get("ts_code") or "")
        return {ts_code, ts_code.split(".")[0], str(item.get("symbol") or "")}

    def _prediction_item_allowed(self, item: Dict[str, Any], monitored_codes: Dict[str, set], scope: str = "") -> bool:
        rule_key = str(item.get("ruleKey") or "")
        # 同向次数仍参与单股状态判断，但不再生成或展示预测卡片。
        if rule_key == "same_direction_10d":
            return False
        if rule_key in {"severe_10d", "severe_30d"}:
            # 已进入对应严重异动监管期后，该严重规则由重点监控承接；
            # 预测页仅追加普通/同向卡，避免同一规则重复展示。
            if scope in {"today", "next_day"} and self._prediction_code_tokens(item) & monitored_codes.get(rule_key, set()):
                return False
            return True
        if rule_key in {"ordinary_3d", "same_direction_10d"}:
            return bool(self._prediction_code_tokens(item) & monitored_codes.get("__severe_monitor__", set()))
        return False

    def start_scheduler(self) -> None:
        """启动单股计算基础数据的每日更新任务。"""

        if self._market_scheduler and self._market_scheduler.is_alive():
            return
        self._market_scheduler_stop.clear()
        self._market_scheduler = Thread(target=self._market_scheduler_loop, name="market-calc", daemon=True)
        self._market_scheduler.start()
        # 基础资料同步与行情计算解耦，服务重启即可补齐价格限制等字段，
        # 且不会篡改 market_calc_job 的行情成功日期。
        self._stock_basic_sync_thread = Thread(
            target=self._sync_stock_basic_on_startup,
            name="stock-basic-sync",
            daemon=True,
        )
        self._stock_basic_sync_thread.start()
        # 规则升级和显式强制重建共用同一任务，避免两个全量任务并行写库。
        Thread(target=self._rebuild_predictions_if_rule_changed, name="prediction-rebuild", daemon=True).start()

    def rebuild_formal_history(self) -> Dict[str, Any]:
        """重抓最近 31 个有效收盘点并重建正式预测数据集。"""

        completed_trade_date = self._latest_completed_trade_date()
        result = self.update_market_data(
            data_stage="formal",
            allow_non_trade_day=True,
            force_history=True,
            history_end_date=completed_trade_date,
        )
        if result.get("updated") and result.get("tradeDate") == completed_trade_date:
            self._generate_prediction_datasets("formal")
        return result

    def _latest_completed_trade_date(self) -> str:
        """返回最近一个已返回全市场日线的交易日，避免盘中交易日被当作收盘日。"""

        latest_calendar_date = self.client.latest_trade_date()
        calendar = self.client.trade_cal(
            (datetime.strptime(latest_calendar_date, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d"),
            latest_calendar_date,
        )
        opened = sorted(
            str(row.get("cal_date") or "")
            for row in calendar.to_dict("records")
            if int(row.get("is_open", 0)) == 1
        )
        for trade_date in reversed(opened):
            frame = self.client.daily(trade_date=trade_date)
            if frame is not None and not frame.empty and any(
                str(row.get("trade_date") or "") == trade_date
                for row in frame.to_dict("records")
            ):
                return trade_date
        raise RuntimeError("没有找到已完成收盘数据的交易日")

    def _sync_stock_basic_on_startup(self) -> None:
        """启动时强制刷新完整 stock_basic，保证生产元数据可用于预测资格判断。"""

        if not self._calc_repository.available:
            return
        try:
            stocks = self._stock_rows(force=True)
            if not stocks:
                raise RuntimeError("Tushare stock_basic 返回空结果")
            self._calc_repository.upsert_stock_basic(stocks)
            self._stocks_cache = (datetime.now(SHANGHAI_TZ), stocks)
        except Exception as exc:  # noqa: BLE001 - 元数据失败由健康接口暴露，不能阻止服务启动
            self._market_update_error = f"stock_basic 启动同步失败：{exc}"

    def _rebuild_predictions_if_rule_changed(self) -> None:
        if not self._calc_repository.available:
            return
        with self._prediction_rebuild_lock:
            try:
                force_history = str(os.getenv("FORCE_MARKET_HISTORY_REBUILD", "")).strip().lower() in {"1", "true", "yes", "on"}
                records = [self._calc_repository.get_prediction(scope) for scope in ("today", "next_day")]
                rules_match = bool(records) and all(
                    record and record.get("rule_version") == PREDICTION_RULE_VERSION
                    for record in records
                )
                if rules_match and not force_history:
                    return
                try:
                    formal = self.rebuild_formal_history()
                except Exception as exc:  # noqa: BLE001 - keep initial snapshot fallback available
                    self._prediction_last_error["today"] = f"正式行情刷新失败：{exc}"
                    formal = {"updated": False, "reason": str(exc)}
                if formal.get("updated"):
                    return
                latest_date, stage = self._latest_data_stage_date()
                refreshed_initial = False
                if not force_history:
                    today = self._now().strftime("%Y%m%d")
                    if latest_date == today and stage == "initial":
                        refreshed = self.update_initial_data()
                        if refreshed.get("updated") and refreshed.get("tradeDate") == today:
                            latest_date, stage = self._latest_data_stage_date()
                            refreshed_initial = True
                if latest_date and refreshed_initial:
                    self._generate_prediction_datasets(stage)
            except Exception as exc:  # noqa: BLE001 - 重建失败不影响旧数据读取
                self._prediction_last_error["today"] = f"预测规则重建失败：{exc}"

    def close(self) -> None:
        """停止每日更新和预测线程池。"""

        self._market_scheduler_stop.set()
        self._prediction_executor.shutdown(wait=False)

    def realtime_quotes(self, ts_codes: Iterable[str]) -> Dict[str, Any]:
        """透传一批盘中行情；只在本次请求内使用，不写入数据库或缓存。"""

        requested: List[str] = []
        seen = set()
        for value in ts_codes:
            code = str(value or "").strip().upper()
            if code and code not in seen:
                requested.append(code)
                seen.add(code)
        quotes = self._realtime.fetch_many(requested)
        updated = [str(item.get("updatedAt") or "") for item in quotes.values() if item.get("updatedAt")]
        return {
            "quotes": quotes,
            "requestedCount": len(requested),
            "returnedCount": len(quotes),
            "quoteUpdatedAt": max(updated, default=""),
            "persisted": False,
        }

    def set_clock(self, clock: Any) -> None:
        """Inject a Shanghai-time clock for deterministic phase tests."""

        self._clock = clock
        self._prediction_state_cache = None

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=SHANGHAI_TZ)
        return value.astimezone(SHANGHAI_TZ)

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
        exchange = str(item.get("exchange") or item.get("market") or "")
        board = str(item.get("board") or _board_label(item.get("market") or item.get("exchange"), symbol))
        price_limit = _price_limit_enabled(item, board, exchange)
        computed_initials = _name_initials(name)
        return {
            "ts_code": str(item.get("ts_code") or ""),
            "symbol": symbol,
            "name": name,
            "market": exchange,
            "marketLabel": _market_label(exchange),
            "board": board,
            "isST": bool(item.get("isST", name.upper().startswith("ST") or name.startswith("*ST"))),
            "nameInitials": computed_initials or str(item.get("nameInitials") or item.get("name_initials") or ""),
            "list_date": item.get("list_date"),
            "list_status": item.get("list_status", "L"),
            "delist_date": item.get("delist_date"),
            "priceLimitEnabled": None if price_limit is None else bool(price_limit),
        }

    @staticmethod
    def _prediction_stock_eligible(stock: Dict[str, Any], target_trade_date: str = "") -> bool:
        """预测只接收正常交易股票；缺失价格限制字段时按正常股票处理。"""

        if not stock or bool(stock.get("isST")):
            return False
        if str(stock.get("list_status") or "L") != "L":
            return False
        if stock.get("priceLimitEnabled") is False:
            return False
        name = str(stock.get("name") or "")
        if "退" in name:
            return False
        list_date = str(stock.get("list_date") or "").replace("-", "")
        if list_date and target_trade_date and list_date >= target_trade_date:
            return False
        return True

    def _prediction_record(self, scope: str, target_trade_date: str = "") -> Optional[Dict[str, Any]]:
        if not self._calc_repository.available:
            return None
        getter = self._calc_repository.get_prediction
        try:
            return getter(scope, target_trade_date=target_trade_date or None)
        except TypeError:
            # 保持本地旧仓储适配器和测试替身可用。
            return getter(scope)

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """按代码、名称和首字母做归一化匹配，并按匹配质量稳定排序。"""

        # 搜索输入可能来自微信复制文本，统一去掉空白、连接符和点号，避免
        # ``601881.SH``、``601881 SH``、``601881`` 被分成不同匹配结果。
        value = re.sub(r"[\s._-]+", "", str(query or "")).casefold()
        if not value:
            return {"items": [], "hasMore": False, "total": 0}
        ranked = []
        for index, item in enumerate(self._stock_rows()):
            symbol = re.sub(r"[\s._-]+", "", str(item.get("symbol") or "")).casefold()
            ts_code = re.sub(r"[\s._-]+", "", str(item.get("ts_code") or "")).casefold()
            name = re.sub(r"\s+", "", str(item.get("name") or "")).casefold()
            initials = re.sub(r"\s+", "", str(item.get("nameInitials") or "")).casefold()
            # 代码支持截图所示的顺序模糊匹配，例如 302 可匹配 603020；
            # 但连续代码、精确代码始终优先于跨位匹配。
            candidates = []
            for candidate in (symbol, ts_code):
                positions = _subsequence_positions(value, candidate)
                if positions is None:
                    continue
                contiguous = value in candidate
                prefix = candidate.startswith(value)
                if value == candidate:
                    rank = 0
                elif prefix:
                    rank = 1
                elif contiguous:
                    rank = 2
                else:
                    rank = 3
                span = positions[-1] - positions[0] if positions else 0
                candidates.append((rank, positions[0] if positions else 0, span, candidate))
            for candidate, base_rank in ((name, 4), (initials, 5)):
                positions = _subsequence_positions(value, candidate)
                if positions is None:
                    continue
                contiguous = value in candidate
                prefix = candidate.startswith(value)
                rank = base_rank - 2 if prefix else base_rank - 1 if contiguous else base_rank
                span = positions[-1] - positions[0] if positions else 0
                candidates.append((rank, positions[0] if positions else 0, span, candidate))
            if not candidates:
                continue
            rank, position, span, matched = min(candidates)
            ranked.append((rank, position, span, len(matched), symbol, str(item.get("name") or ""), index, item))
        ranked.sort(key=lambda entry: entry[:7])
        return {
            "items": [entry[7] for entry in ranked[:limit]],
            "hasMore": len(ranked) > limit,
            "total": len(ranked),
        }

    def history_coverage(self, offset: int = 0, limit: int = 100) -> Dict[str, Any]:
        """只读检查活跃股票池最近 30 个共同收益区间的覆盖。"""

        repository = self._calc_repository
        if not repository.available:
            raise CalculationDataUnavailable("MySQL 计算数据仓库不可用，无法审计历史覆盖")
        try:
            stocks, calculations, indexes = repository.history_coverage_snapshot()
        except Exception as exc:  # noqa: BLE001
            raise CalculationDataUnavailable("读取历史计算数据失败，覆盖审计未完成") from exc
        if stocks is None or calculations is None or indexes is None:
            raise CalculationDataUnavailable("历史计算数据不可读，覆盖审计未完成")

        categories: Dict[str, int] = {}
        findings: List[Dict[str, Any]] = []
        complete_count = 0
        special_stock_count = 0
        index_dates_by_code = {
            code: {self._date_key(item.get("date")) for item in self._vector_entries(item.get("index_return_vector")) if self._date_key(item.get("date"))}
            for code, item in indexes.items()
        }
        latest_dates = [
            self._date_key(item.get("as_of_trade_date"))
            for item in indexes.values()
            if self._date_key(item.get("as_of_trade_date"))
        ]
        latest_date = max(latest_dates, default="")
        expected_market_dates = sorted({
            date for dates in index_dates_by_code.values() for date in dates if date <= latest_date
        })[-30:]
        suspension_pairs = set()
        suspension_source_available = False
        if expected_market_dates and latest_date:
            try:
                suspension_rows = []
                for start in range(0, len(expected_market_dates), 10):
                    chunk = expected_market_dates[start:start + 10]
                    suspension_frame = self.client.suspend_d(chunk[0], chunk[-1])
                    chunk_rows = suspension_frame.to_dict("records") if hasattr(suspension_frame, "to_dict") else list(suspension_frame or [])
                    if len(chunk_rows) >= 5000:
                        raise RuntimeError("停复牌数据超过单次接口行数上限")
                    suspension_rows.extend(chunk_rows)
                suspension_pairs = {
                    (str(row.get("ts_code") or "").upper(), self._date_key(row.get("trade_date")))
                    for row in suspension_rows
                    if str(row.get("suspend_type") or "").upper() == "S"
                }
                suspension_source_available = True
            except Exception:  # noqa: BLE001 - 状态源不可用时保留明确的未判定结果
                suspension_source_available = False

        def record(stock: Dict[str, Any], category: str, details: Dict[str, Any]) -> None:
            categories[category] = categories.get(category, 0) + 1
            findings.append({
                "ts_code": stock.get("ts_code", ""), "name": stock.get("name", ""),
                "category": category, "listDate": self._date_key(stock.get("list_date")),
                **details,
            })

        for stock in stocks:
            if bool(stock.get("isST")) or stock.get("priceLimitEnabled") is False:
                special_stock_count += 1
            ts_code = str(stock.get("ts_code") or "")
            calc = calculations.get(ts_code)
            if not calc:
                list_date = self._date_key(stock.get("list_date"))
                listing_dates = [date for date in expected_market_dates if list_date and date >= list_date]
                category = "recent_listing_short_history" if list_date and len(listing_dates) < 30 else "missing_stock_vector"
                missing_dates = listing_dates if category == "recent_listing_short_history" else expected_market_dates
                suspended_dates = [date for date in missing_dates if (ts_code.upper(), date) in suspension_pairs]
                record(stock, category, {
                    "stockReturns": 0,
                    "commonReturns": 0,
                    "expectedReturns": len(expected_market_dates),
                    "missingDates": missing_dates,
                    "suspendedDates": suspended_dates,
                    "statusResolution": "Tushare suspend_d 已核验" if suspension_source_available else "停复牌状态源不可用，停牌与上游缺记录无法区分",
                })
                continue
            index_code = _index_code_for_stock(stock)
            index = indexes.get(index_code)
            stock_vector = self._vector_entries(calc.get("stock_return_vector"))
            if not index:
                record(stock, "missing_index_vector", {
                    "indexCode": index_code, "stockReturns": len(stock_vector), "commonReturns": 0,
                })
                continue

            index_vector = self._vector_entries(index.get("index_return_vector"))
            stock_dates = {self._date_key(item.get("date")) for item in stock_vector if self._date_key(item.get("date"))}
            all_index_dates = index_dates_by_code.get(index_code, set())
            expected_dates = expected_market_dates
            common_dates = sorted(stock_dates & set(expected_dates))
            stock_as_of = self._date_key(calc.get("as_of_trade_date"))
            index_as_of = self._date_key(index.get("as_of_trade_date"))
            missing_dates = [date for date in expected_dates if date not in stock_dates]

            missing_index_dates = [date for date in expected_dates if date not in all_index_dates]
            if len(expected_dates) < 30 or missing_index_dates:
                category = "index_history_incomplete"
            elif len(common_dates) >= 30 and stock_as_of == latest_date and index_as_of == latest_date:
                complete_count += 1
                continue
            else:
                list_date = self._date_key(stock.get("list_date"))
                listing_dates = [date for date in expected_dates if list_date and date >= list_date]
                if len(listing_dates) < 30 and (not list_date or list_date > expected_dates[0]):
                    category = "recent_listing_short_history"
                elif stock_as_of < latest_date:
                    category = "tail_market_data_missing_unclassified"
                elif stock_as_of != index_as_of:
                    category = "stock_index_asof_mismatch"
                else:
                    category = "market_date_gap_unclassified"
                suspended_dates = [
                    date for date in missing_dates
                    if (ts_code.upper(), date) in suspension_pairs
                ]
                source_missing_dates = [date for date in missing_dates if date not in suspended_dates]
                if category not in {"recent_listing_short_history", "index_history_incomplete", "stock_index_asof_mismatch"} and missing_dates:
                    if suspension_source_available and suspended_dates and not source_missing_dates:
                        category = "suspended_history_short"
                    elif suspension_source_available and suspended_dates and source_missing_dates:
                        category = "suspension_and_source_gaps"
                    elif suspension_source_available:
                        category = "upstream_market_data_missing"
                    else:
                        category = "market_date_gap_unclassified"
            record(stock, category, {
                "indexCode": index_code,
                "stockAsOf": stock_as_of,
                "indexAsOf": index_as_of,
                "stockReturns": len(stock_dates),
                "indexReturns": len(expected_dates),
                "commonReturns": len(common_dates),
                "missingDates": missing_dates,
                "missingIndexDates": missing_index_dates,
                "suspendedDates": [date for date in missing_dates if (ts_code.upper(), date) in suspension_pairs],
                "statusResolution": "Tushare suspend_d 已核验" if suspension_source_available else "停复牌状态源不可用，停牌与上游缺记录无法区分",
            })

        findings.sort(key=lambda item: (item["category"], item["ts_code"]))
        start = max(0, int(offset))
        page_size = max(1, min(500, int(limit)))
        return {
            "asOfTradeDate": latest_date,
            "requiredReturnCount": 30,
            "activeStockCount": len(stocks),
            "completeStockCount": complete_count,
            "incompleteStockCount": len(findings),
            "specialStateStockCount": special_stock_count,
            "suspensionStatusSource": "tushare.suspend_d" if suspension_source_available else "unavailable",
            "categoryCounts": categories,
            "classificationLimit": "停牌与上游缺记录需要独立停复牌状态源才能最终区分；本接口不猜测。",
            "items": findings[start:start + page_size],
            "pagination": {"offset": start, "limit": page_size, "total": len(findings)},
        }

    def calculation_bases(self, ts_codes: Optional[List[str]] = None,
                          offset: int = 0, limit: int = 100) -> Dict[str, Any]:
        """直接读取数据库中的股票/指数收益基线，供审计和本地模拟使用。"""

        def scalar(value: Any) -> Any:
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            if isinstance(value, datetime):
                return self._format_prediction_datetime(value)
            try:
                return float(value)
            except (TypeError, ValueError):
                return str(value)

        repository = self._calc_repository
        if not repository.available:
            raise CalculationDataUnavailable("MySQL 计算数据仓库不可用，无法读取计算基线")
        try:
            payload = repository.calculation_bases(ts_codes=ts_codes, offset=offset, limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise CalculationDataUnavailable("读取计算基线失败") from exc
        if not payload:
            raise CalculationDataUnavailable("计算基线不可读")
        indexes = {str(item.get("index_code")): item for item in payload.get("indexes", [])}
        items: List[Dict[str, Any]] = []
        referenced_indexes = set()
        for stock in payload.get("stocks", []):
            index_code = _index_code_for_stock(stock)
            referenced_indexes.add(index_code)
            items.append({
                "ts_code": stock.get("ts_code", ""),
                "symbol": stock.get("symbol", ""),
                "name": stock.get("name", ""),
                "market": stock.get("market", ""),
                "board": stock.get("board", ""),
                "isST": bool(stock.get("isST")),
                "listStatus": stock.get("list_status", ""),
                "listDate": stock.get("list_date", ""),
                "delistDate": stock.get("delist_date", ""),
                "priceLimitEnabled": stock.get("priceLimitEnabled"),
                "indexCode": index_code,
                "asOfTradeDate": stock.get("as_of_trade_date", ""),
                "latestClose": scalar(stock.get("latest_close")),
                "returnVector": stock.get("stock_return_vector") or [],
                "deviations": {
                    "3": scalar(stock.get("deviation_3")),
                    "10": scalar(stock.get("deviation_10")),
                    "30": scalar(stock.get("deviation_30")),
                },
                "sameDirection": {
                    "up": int(stock.get("same_direction_up") or 0),
                    "down": int(stock.get("same_direction_down") or 0),
                },
                "dataStage": stock.get("data_stage", "formal"),
                "quoteUpdatedAt": scalar(stock.get("quote_updated_at")),
                "updatedAt": scalar(stock.get("updated_at")),
            })
        index_items = []
        for code in sorted(referenced_indexes):
            index = indexes.get(code)
            if not index:
                continue
            index_items.append({
                "indexCode": index.get("index_code", ""),
                "indexName": index.get("index_name", ""),
                "asOfTradeDate": index.get("as_of_trade_date", ""),
                "latestClose": scalar(index.get("latest_close")),
                "returnVector": index.get("index_return_vector") or [],
                "dataStage": index.get("data_stage", "formal"),
                "quoteUpdatedAt": scalar(index.get("quote_updated_at")),
                "updatedAt": scalar(index.get("updated_at")),
            })
        dates = [str(item.get("asOfTradeDate") or "") for item in items + index_items]
        return {
            "items": items,
            "indexes": index_items,
            "activeStockCount": int(payload.get("activeStockCount") or 0),
            "returnedStockCount": len(items),
            "asOfTradeDate": max(dates, default=""),
            "pagination": {"offset": offset, "limit": limit, "total": int(payload.get("activeStockCount") or 0)},
            "source": "stock_calc_base + index_calc_base",
            "containsRawDaily": False,
            "containsIntradaySnapshot": False,
        }

    def persisted_dataset_statuses(self) -> Dict[str, Any]:
        repository = self._calc_repository
        if not repository.available:
            raise CalculationDataUnavailable("MySQL 计算数据仓库不可用，无法读取数据集状态")
        try:
            rows = repository.prediction_dataset_statuses() or []
        except Exception as exc:  # noqa: BLE001
            raise CalculationDataUnavailable("读取预测数据集状态失败") from exc
        for row in rows:
            for key in ("updated_at", "dataset_quote_updated_at"):
                row[key] = self._format_prediction_datetime(row.get(key))
        return {"items": rows, "count": len(rows), "source": "prediction_dataset"}

    def persisted_jobs(self) -> Dict[str, Any]:
        repository = self._calc_repository
        if not repository.available:
            raise CalculationDataUnavailable("MySQL 计算数据仓库不可用，无法读取任务状态")
        try:
            payload = repository.jobs_snapshot() or {"market": [], "prediction": []}
        except Exception as exc:  # noqa: BLE001
            raise CalculationDataUnavailable("读取任务状态失败") from exc
        for rows in payload.values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                for key in ("last_attempt_at", "started_at", "finished_at"):
                    if key in row:
                        row[key] = self._format_prediction_datetime(row.get(key))
        return {**payload, "source": "market_calc_job + prediction_job"}

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

        return [datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d") for value in self._future_trade_date_values(latest, count)]

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
            target = up
            distance = max(0.0, target - value) if value is not None else None
            triggered = value is not None and value >= up
            progress = min(100, max(0.0, value) / max(target, 0.01) * 100) if value is not None else 0
            alerts.append({
                "title": f"{days} 日偏离",
                "forecast": "已触发" if triggered else (f"还差 {distance:.2f}%" if distance is not None else "数据不足"),
                "detail": f"当前 {_pct(value)}，上涨阈值 +{up:.0f}%" if value is not None else "暂无足够交易日数据",
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
    def _return_entries_from_bars(stock_bars: List[PriceBar], index_bars: List[PriceBar]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """把冷启动路径的收盘价转换成与计算仓库完全相同的日期化收益向量。"""

        stock_entries: List[Dict[str, Any]] = []
        index_entries: List[Dict[str, Any]] = []
        for stock_previous, stock_current, index_previous, index_current in zip(stock_bars, stock_bars[1:], index_bars, index_bars[1:]):
            if stock_current.trade_date != index_current.trade_date:
                continue
            stock_entries.append({"date": stock_current.trade_date.isoformat(), "return": (stock_current.close / stock_previous.close - 1) * 100})
            index_entries.append({"date": index_current.trade_date.isoformat(), "return": (index_current.close / index_previous.close - 1) * 100})
        return stock_entries[-30:], index_entries[-30:]

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
        # 3 日普通异动是警示；10 日同向达到次数阈值属于 10 日严重异常。
        if same_up >= rule.severe_same_direction_count:
            return "触及", "severe"
        if deviations.get(3) is not None and deviations[3] >= rule.ordinary_deviation:
            return "警示", "risk"
        return "正常", "safe"

    @staticmethod
    def _status_reason_for_metrics(deviations: Dict[int, Optional[float]], same_up: int, rule: Any) -> str:
        reasons = []
        if deviations.get(30) is not None and rule.severe_30d_threshold.triggered(deviations[30]):
            reasons.append("30 日严重异动")
        if deviations.get(10) is not None and rule.severe_10d_threshold.triggered(deviations[10]):
            reasons.append("10 日严重异动")
        if same_up >= rule.severe_same_direction_count:
            reasons.append("10 日同向次数")
        if deviations.get(3) is not None and deviations[3] >= rule.ordinary_deviation:
            reasons.append("3 日普通异动")
        return "、".join(f"{reason}已触发" for reason in reasons)

    def _active_monitor_for_stock(self, stock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """返回当前仍在监管期内的公告事实，不把监管状态混入行情异动计算。"""

        if not self._monitor_provider:
            return None
        symbol = str(stock.get("symbol") or stock.get("ts_code") or "").split(".")[0]
        today = self._now().strftime("%Y-%m-%d")
        try:
            snapshot = self._monitor_provider() or {}
            matches = []
            for item in snapshot.get("items", []):
                item_symbol = str(item.get("symbol") or item.get("ts_code") or "").split(".")[0]
                if item_symbol != symbol:
                    continue
                dates = {str(value)[:10] for value in (item.get("monitorDates") or [])}
                end_date = str(item.get("monitorEndDate") or "")[:10]
                if dates and today not in dates:
                    continue
                if not dates and end_date and end_date < today:
                    continue
                matches.append(item)
            if not matches:
                return None
            item = sorted(matches, key=lambda value: str(value.get("monitorEndDate") or ""), reverse=True)[0]
            return {
                "active": True,
                "monitorType": item.get("monitorType") or "重点监控",
                "riskTone": item.get("riskTone") or "ordinary",
                "title": item.get("title") or "",
                "monitorStartDate": item.get("monitorStartDate") or "",
                "monitorEndDate": item.get("monitorEndDate") or "",
                # 公告后的首个监管交易日是新的监管事实起点；规则累计从
                # 该日起的下一个有效收益区间开始，不能把公告前涨幅跨周期带入。
                "monitorDates": [str(value)[:10] for value in (item.get("monitorDates") or [])],
                "days": item.get("days"),
                "daysTotal": item.get("daysTotal"),
            }
        except Exception:
            return None

    @staticmethod
    def _monitor_rule_reset_date(monitoring: Optional[Dict[str, Any]]) -> str:
        """返回严重异动公告后的规则累计起点。

        监管交易日序列的第一个日期是公告后首个交易日，下一交易日才是
        新一轮偏离累计的首个有效收益日，因此取 ``monitorDates[1]``。
        风险提示不改变行情规则累计起点。
        """

        if not monitoring or str(monitoring.get("riskTone") or "") not in {"severe-10d", "severe-30d"}:
            return ""
        dates = [str(value).replace("-", "")[:8] for value in (monitoring.get("monitorDates") or []) if value]
        return dates[1] if len(dates) >= 2 else ""

    def _latest_rule_monitor_for_stock(self, stock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """读取该股票最近一条严重异动公告事实（不受当前监管期过滤）。"""

        if not self._monitor_provider:
            return None
        symbol = str(stock.get("symbol") or stock.get("ts_code") or "").split(".")[0]
        try:
            snapshot = self._monitor_provider() or {}
            matches = []
            for item in snapshot.get("items", []):
                item_symbol = str(item.get("symbol") or item.get("ts_code") or "").split(".")[0]
                if item_symbol != symbol or str(item.get("riskTone") or "") not in {"severe-10d", "severe-30d"}:
                    continue
                matches.append(item)
            if not matches:
                return None
            item = max(matches, key=lambda value: (
                str(value.get("announcementDate") or value.get("monitorStartDate") or ""),
                str(value.get("monitorEndDate") or ""),
            ))
            return {
                "riskTone": item.get("riskTone") or "",
                "monitorDates": [str(value)[:10] for value in (item.get("monitorDates") or [])],
            }
        except Exception:
            return None

    @classmethod
    def _trim_vectors_after_rule_reset(
        cls,
        stock: List[Dict[str, Any]],
        index: List[Dict[str, Any]],
        reset_date: str,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """应用公告后的统一规则重置，并保留审计元数据。"""

        if not reset_date:
            return stock, index, {"ruleResetDate": ""}
        stock_trimmed = [item for item in stock if cls._date_key(item.get("date")) >= reset_date]
        index_trimmed = [item for item in index if cls._date_key(item.get("date")) >= reset_date]
        return stock_trimmed, index_trimmed, {"ruleResetDate": reset_date}

    def _rule_vectors_for_stock(
        self,
        stock: Dict[str, Any],
        stock_vector: List[Dict[str, Any]],
        index_vector: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], Optional[Dict[str, Any]]]:
        """统一应用停牌重置和严重异动公告后的规则重置。"""

        stock_vector, index_vector, suspension = self._trim_vectors_after_suspension(stock_vector, index_vector)
        monitoring = self._active_monitor_for_stock(stock)
        reset_monitor = self._latest_rule_monitor_for_stock(stock) or monitoring
        reset_date = self._monitor_rule_reset_date(reset_monitor)
        stock_vector, index_vector, rule_reset = self._trim_vectors_after_rule_reset(
            stock_vector, index_vector, reset_date
        )
        return stock_vector, index_vector, {**suspension, **rule_reset}, monitoring

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
        """计算最近有效窗口内的最大上涨偏离及实际窗口。

        10/30 日规则的“连续 N 个交易日内”是价格日回看上限：在最近 N 个共同
        交易日内枚举以当前端点结束的 1..N-1 个连续有效收益率区间；3 日规则
        枚举 1..3 个区间。三项都只取最大上涨偏离并返回实际窗口。
        这必须与
        ``server.core.abnormal_rules._best_effective_deviation`` 保持一致，
        否则单股和预测会出现实际窗口分叉。
        """
        if not stock or not index:
            return None, None
        stock_map = {item.get("date"): float(item.get("return")) for item in stock if item.get("date")}
        index_map = {item.get("date"): float(item.get("return")) for item in index if item.get("date")}
        dates = sorted(set(stock_map) & set(index_map))
        if len(dates) < 1:
            return None, None
        # 10/30 日规则由价格日上限对应 N-1 个收益区间；3 日普通规则也
        # 统一采用同一套“当前端点向前取最大上涨区间”算法，最多取 3 个区间。
        upper_limit = max_window - 1 if max_window in (10, 30) else max_window
        upper = min(upper_limit, len(dates))
        if upper < 1:
            return None, None
        lower = 1
        windows = list(range(lower, upper + 1)) if upper >= lower else [upper]
        results = []
        for window in windows:
            trailing_dates = dates[-window:]
            value = cls._compound(stock_map[day] for day in trailing_dates) - cls._compound(index_map[day] for day in trailing_dates)
            results.append((value, window))
        positive = [item for item in results if item[0] > 0]
        if positive:
            return max(positive, key=lambda item: (item[0], -item[1]))
        return min(results, key=lambda item: (item[0], item[1]))

    @classmethod
    def _required_up_percent(
        cls,
        stock: List[Dict[str, Any]],
        index: List[Dict[str, Any]],
        actual_window: int,
        target_deviation: float,
        forecast_scope: str = "next_day",
    ) -> Optional[float]:
        """反推目标交易日股票需要达到的理论日涨幅。

        ``today`` 卡是在当前交易日收盘前反推：最新股票日收益是待求的
        未知量，所以只使用窗口内前 ``N-1`` 个股票收益；指数的最新日收益
        已知，仍纳入基准。``next_day`` 卡则使用完整当前窗口，再反推下一
        交易日的股票收益。两条路径共用同一复合收益公式，但未知日不同，
        不能把当日卡也按“追加下一日”计算。
        """
        stock_map = {item.get("date"): float(item.get("return")) for item in stock if item.get("date")}
        index_map = {item.get("date"): float(item.get("return")) for item in index if item.get("date")}
        dates = sorted(set(stock_map) & set(index_map))[-actual_window:]
        if len(dates) < actual_window:
            return None
        stock_dates = dates[:-1] if forecast_scope == "today" else dates
        if not stock_dates and forecast_scope == "today":
            return None
        stock_total = cls._compound(stock_map[day] for day in stock_dates)
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
        up_count = 0
        end = 2
        while end < len(stock_values):
            deviation = cls._compound(stock_values[end - 2 : end + 1]) - cls._compound(index_values[end - 2 : end + 1])
            if deviation >= rule.ordinary_deviation:
                up_count += 1
            # 命中的 3 日区间被消费；下一个事件必须从未消费的交易日开始，
            # 避免相邻滚动窗口把同一段连续行情重复计数。
            end += 3 if deviation >= rule.ordinary_deviation else 1
        return {
            "deviations": deviations,
            "window_lengths": window_lengths,
            "up": up_count,
            # 保留字段以兼容旧页面/日志，但下跌不再是业务统计结果。
            "down": 0,
            "same": up_count,
        }

    @staticmethod
    def _prediction_sort_key(item: Dict[str, Any]) -> tuple:
        """按产品顺序返回预测卡排序键：30 日、10 日、其他；同类按触发条件升序。"""
        priority = {"severe_30d": 0, "severe_10d": 1}.get(str(item.get("ruleKey") or ""), 2)
        value = item.get("triggerValue")
        try:
            trigger = float(re.search(r"[-+]?\d+(?:\.\d+)?", str(value)).group(0))
        except (AttributeError, ValueError):
            trigger = float("inf")
        return priority, trigger, str(item.get("ts_code") or ""), str(item.get("predictionKey") or "")

    @classmethod
    def _sort_prediction_items(cls, items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(list(items or []), key=cls._prediction_sort_key)

    @staticmethod
    def _quote_date_string(quote: Optional[Dict[str, Any]]) -> str:
        value = str((quote or {}).get("updatedAt") or "")
        return value[:10] if len(value) >= 10 else ""

    @classmethod
    def _quotes_share_snapshot(cls, stock_quote: Optional[Dict[str, Any]], index_quote: Optional[Dict[str, Any]]) -> bool:
        """只允许同源、同交易日且时间相差不超过五分钟的行情共同参与计算。"""
        if not stock_quote or not index_quote:
            return False
        stock_time = str(stock_quote.get("updatedAt") or "")
        index_time = str(index_quote.get("updatedAt") or "")
        if not stock_time or not index_time:
            return False
        if cls._quote_date_string(stock_quote) != cls._quote_date_string(index_quote):
            return False
        stock_source = str(stock_quote.get("source") or "")
        index_source = str(index_quote.get("source") or "")
        if stock_source and index_source and stock_source != index_source:
            return False
        try:
            stock_dt = datetime.fromisoformat(stock_time.replace("Z", "+00:00"))
            index_dt = datetime.fromisoformat(index_time.replace("Z", "+00:00"))
        except ValueError:
            return stock_time[:10] == index_time[:10] and len(stock_time) >= 19 and len(index_time) >= 19
        if stock_dt.tzinfo is None:
            stock_dt = stock_dt.replace(tzinfo=SHANGHAI_TZ)
        if index_dt.tzinfo is None:
            index_dt = index_dt.replace(tzinfo=SHANGHAI_TZ)
        return abs((stock_dt - index_dt).total_seconds()) <= 300

    @classmethod
    def _vector_latest_date(cls, vector: List[Dict[str, Any]]) -> str:
        return max((cls._date_key(item.get("date")) for item in vector if item.get("date")), default="")

    @classmethod
    def _apply_quote_to_vector(cls, entries: List[Dict[str, Any]], latest_close: Any, quote: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把当前报价转为一个当日收益率，替换或追加最后一个向量元素。"""

        if not quote:
            return list(entries)
        try:
            current = float(quote.get("current"))
        except (TypeError, ValueError):
            return list(entries)
        if current <= 0:
            return list(entries)
        quote_date_text = cls._quote_date_string(quote)
        quote_date = "".join(character for character in quote_date_text if character.isdigit())[:8]
        if len(quote_date) != 8:
            return list(entries)
        result = list(entries)
        last_date = "".join(character for character in str(result[-1].get("date") or "") if character.isdigit())[:8] if result else ""
        try:
            previous_close = float(quote.get("preClose") or latest_close)
        except (TypeError, ValueError):
            return list(entries)
        if previous_close <= 0:
            return list(entries)
        if result and last_date == quote_date:
            if not quote.get("preClose") and len(result) >= 2:
                last_return = float(result[-1]["return"])
                previous_close = previous_close / (1.0 + last_return / 100.0)
            result[-1] = {"date": quote_date, "return": (current / previous_close - 1.0) * 100.0}
        elif not result or quote_date > last_date:
            result.append({"date": quote_date, "return": (current / previous_close - 1.0) * 100.0})
        return result[-30:]

    def _refresh_initial_snapshot_after_formal_failure(self, trade_date: str) -> bool:
        """正式行情未就绪时，用最新实时初态快照重建预测，避免沿用旧初态数据。"""
        latest_date, stage = self._latest_data_stage_date()
        if latest_date != trade_date or stage != "initial":
            return False
        result = self.update_initial_data()
        if not result.get("updated") or result.get("tradeDate") != trade_date:
            return False
        self._generate_prediction_datasets("initial")
        return True

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
        stored_stock_date = self._date_key(calc.get("as_of_trade_date"))
        stored_index_date = self._date_key(index.get("as_of_trade_date"))
        stock_vector_date = self._vector_latest_date(stock_vector)
        index_vector_date = self._vector_latest_date(index_vector)
        if (
            not stored_stock_date
            or stored_stock_date != stored_index_date
            # 新上市首个交易日可能还没有收益率点；保留空向量，详情页展示
            # 股票/指数和“暂无足够交易日”的预警，而不是把整张详情页判为不可用。
            or (stock_vector_date and stock_vector_date != stored_stock_date)
            or (index_vector_date and index_vector_date != stored_index_date)
        ):
            return None
        stock_vector, index_vector, suspension, monitoring = self._rule_vectors_for_stock(
            stock, stock_vector, index_vector
        )
        board = BOARD_BY_MARKET.get(stock["board"], Board.MAIN)
        rule = BOARD_RULES[board]
        quotes = self._realtime.fetch_many([stock["ts_code"], index_code])
        quote = quotes.get(stock["ts_code"].upper())
        index_quote = quotes.get(index_code.upper())
        realtime = self._quotes_share_snapshot(quote, index_quote)
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
        status_reason = self._status_reason_for_metrics(deviations, metrics["up"], rule)
        display_status = "监管中" if monitoring else ("状态安全" if status == "正常" else "状态异常")
        display_status_class = "monitoring" if monitoring else status_class
        market_status_display = f"已触发 · {status_reason}" if status_reason else "当前行情规则未触发"
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
            {"title": "3 日偏离", "value": _pct(deviations[3]), "actualWindow": metrics["window_lengths"][3], "target": f"上涨阈值 +{rule.ordinary_deviation:.0f}%" if deviations[3] is not None else "数据不足（无有效共同收益区间）", "className": "risk" if deviations[3] is not None and deviations[3] >= rule.ordinary_deviation else "safe" if deviations[3] is not None else "neutral"},
            {"title": "10 日偏离", "value": _pct(deviations[10]), "actualWindow": metrics["window_lengths"][10], "target": f"上涨阈值 +{rule.severe_10d_threshold.up:.0f}%" if deviations[10] is not None else "数据不足（无有效共同收益区间）", "className": "risk" if deviations[10] is not None and rule.severe_10d_threshold.triggered(deviations[10]) else "safe" if deviations[10] is not None else "neutral"},
            {"title": "30 日偏离", "value": _pct(deviations[30]), "actualWindow": metrics["window_lengths"][30], "target": f"上涨阈值 +{rule.severe_30d_threshold.up:.0f}%" if deviations[30] is not None else "数据不足（无有效共同收益区间）", "className": "risk" if deviations[30] is not None and rule.severe_30d_threshold.triggered(deviations[30]) else "safe" if deviations[30] is not None else "neutral"},
            {"title": "10 日同向", "value": "", "up": str(metrics["up"]), "down": str(metrics["down"]), "target": f"阈值 {rule.severe_same_direction_count} 次", "className": "risk" if same_count >= rule.severe_same_direction_count else "safe"},
        ]
        return {
            **stock,
            "status": status,
            "marketStatus": status,
            "marketStatusClass": status_class,
            "marketStatusDisplay": market_status_display,
            "statusReason": status_reason,
            "statusDisplay": display_status,
            "statusClass": display_status_class,
            "statusIcon": "!" if display_status_class != "safe" else "✓",
            "monitoring": monitoring,
            "currentPrice": f"{float(current_price):.2f}" if current_price is not None else "--",
            "change": _pct(current_change),
            "tradeDate": latest_date,
            "futureTradeDates": future_dates,
            "warnings": warnings,
            "alerts": self._build_alerts(deviations, severe, board),
            "simulationBase": {"stock": self._vector_numbers(stock_vector), "index": self._vector_numbers(index_vector)},
            "calculationInput": {"stock": stock_vector, "index": index_vector, "ordinaryDeviation": rule.ordinary_deviation, "severe10": {"up": rule.severe_10d_threshold.up, "down": rule.severe_10d_threshold.down}, "severe30": {"up": rule.severe_30d_threshold.up, "down": rule.severe_30d_threshold.down}, "sameDirectionThreshold": rule.severe_same_direction_count, "ruleVersion": PREDICTION_RULE_VERSION},
            "simulationThresholds": {
                "3": {"up": rule.ordinary_deviation},
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
                "isComplete": common_days >= 30,
                "commonDays": common_days,
                "requiredCommonDays": 30,
                "suspensionResetDate": suspension.get("resetDate", ""),
                "ruleResetDate": suspension.get("ruleResetDate", ""),
                "suspended": suspension.get("suspended", False),
                "quoteUpdatedAt": display_quote.get("updatedAt", "") if display_quote else "",
                "message": (
                    f"历史数据覆盖 {common_days} 个共同交易日，按现有数据计算"
                    if common_days < 30 else ""
                ) + (("；" if common_days < 30 else "") + "实时行情暂不可用" if not realtime else ""),
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
                        # 休息日只允许把最近已完成交易日的初态补确认为正式收盘数据。
                        # 不把休市日当成行情日，也不在空库时触发全市场初始化。
                        latest = self.client.latest_trade_date()
                        latest_date, latest_stage = self._latest_data_stage_date()
                        if latest_date and latest_date <= latest and latest_stage == "initial":
                            catch_up_slot = now.strftime("%Y%m%d-%H-%M")[:-1]
                            marker = f"rest-formal-{latest}-{catch_up_slot}"
                            if self._market_last_formal_attempt != marker:
                                self._market_last_formal_attempt = marker
                                result = self.update_market_data(data_stage="formal", allow_non_trade_day=True)
                                if result.get("updated") and result.get("tradeDate") == latest:
                                    self._generate_prediction_datasets("formal")
                        self._market_scheduler_stop.wait(60)
                        continue
                    if 15 <= now.hour <= 23:
                        # 容器可能在 15:00 后冷启动；只要当日尚未成功生成初态，
                        # 在当前允许时段补做一次，避免错过整天的初态数据。
                        # 正式数据已成功写入当日时不重复覆盖为初态。
                        initial_marker = f"{today}-initial"
                        latest_stage_date = self._latest_data_stage_date()
                        if latest_stage_date not in ((today, "initial"), (today, "formal")) and self._market_last_formal_attempt != initial_marker:
                            self._market_last_formal_attempt = initial_marker
                            if self.update_initial_data().get("updated"):
                                self._generate_prediction_datasets("initial")
                    if 16 <= now.hour <= 23:
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
                            try:
                                result = self.update_market_data(data_stage="formal")
                            except Exception as exc:  # noqa: BLE001 - formal failure keeps provisional data usable
                                self._market_update_error = str(exc)
                                result = {"updated": False, "reason": str(exc)}
                            if result.get("updated") and result.get("tradeDate") == latest:
                                self._generate_prediction_datasets("formal")
                            else:
                                self._refresh_initial_snapshot_after_formal_failure(today)
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
    def _market_update_allowed(today_is_trade_day: bool, allow_non_trade_day: bool = False) -> bool:
        """仅显式的历史正式回补可以越过当前自然日休市闸门。"""

        return bool(today_is_trade_day or allow_non_trade_day)

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
                row_date = str(trade_date)
                # 初态和正式收盘都写同一个交易日。此时 latest_close 是初态价，
                # 不能拿它作为“前收”再次计算，否则正式涨跌会被写成初态到收盘的
                # 伪收益，甚至在某些回补路径中落成 0%。正式日线自身的 pct_chg
                # 才是该交易日相对前收的权威结果。
                vector = [entry for entry in vector if entry.get("date") != row_date]
                pct_chg = row.get("pct_chg")
                as_of = str(item.get("as_of_trade_date") or "")
                if as_of == row_date and pct_chg is not None:
                    vector.append({"date": row_date, "return": float(pct_chg)})
                elif previous_close and float(previous_close) > 0:
                    vector.append(self._return_entry(row_date, float(previous_close), float(close)))
                item["stock_return_vector"] = vector[-30:]
                item["latest_close"] = float(close)
                item["as_of_trade_date"] = row_date
                item["quote_updated_at"] = f"{row_date[:4]}-{row_date[4:6]}-{row_date[6:]} 15:00:00"

    def update_market_data(self, data_stage: str = "formal", allow_non_trade_day: bool = False, force_history: bool = False, history_end_date: str = "") -> Dict[str, Any]:
        """生成或增量更新最小计算数据，不保存原始日线。"""

        if not self._calc_repository.available:
            return {"updated": False, "reason": "MySQL 计算数据仓库未配置"}
        with self._market_update_lock:
            try:
                self._calc_repository.ensure_schema()
                today = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")
                if not self._market_update_allowed(self._is_trade_day(today), allow_non_trade_day):
                    return {"updated": False, "reason": "今天不是交易日", "tradeDate": ""}
                latest = str(history_end_date or self.client.latest_trade_date()) if force_history else self.client.latest_trade_date()
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
                rebuild_history = bool(force_history)
                if rebuild_history:
                    # 丢弃旧向量，只保留最新 stock_basic；后续用 31 个正式收盘点重建。
                    existing = {}
                    indexes_existing = {}
                bootstrap = not existing or rebuild_history
                if bootstrap:
                    needed_dates = open_dates[-31:]
                else:
                    needed_dates = [item for item in open_dates if item > previous_date]
                    # 初态已经写入当前交易日时，正式任务仍必须重新拉取该日
                    # Tushare 收盘数据，不能因日期相同而提前返回。
                    if data_stage == "formal" and (self._latest_data_stage() == "initial" or allow_non_trade_day) and latest not in needed_dates:
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
                    rows_by_date: Dict[str, Dict[str, Dict[str, Any]]] = {}
                    for trade_date, frame in daily_frames.items():
                        if frame is None or frame.empty:
                            rows_by_date[trade_date] = {}
                            continue
                        rows_by_date[trade_date] = {
                            str(row.get("ts_code") or ""): row
                            for row in frame.to_dict("records")
                            if row.get("ts_code") and row.get("close") is not None and float(row.get("close") or 0) > 0
                        }
                    for item in stocks:
                        rows = [
                            rows_by_date[trade_date][item["ts_code"]]
                            for trade_date in needed_dates
                            if item["ts_code"] in rows_by_date.get(trade_date, {})
                        ]
                        rows.sort(key=lambda row: str(row.get("trade_date")))
                        vector = []
                        for previous, current in zip(rows, rows[1:]):
                            vector.append(self._return_entry(current["trade_date"], float(previous["close"]), float(current["close"])))
                        if rows:
                            row_date = str(rows[-1]["trade_date"])
                            existing[item["ts_code"]] = {"ts_code": item["ts_code"], "as_of_trade_date": row_date, "latest_close": float(rows[-1]["close"]), "stock_return_vector": vector[-30:], "quote_updated_at": f"{row_date[:4]}-{row_date[4:6]}-{row_date[6:]} 15:00:00"}
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
                                "latest_close": float(rows[-1]["close"]), "stock_return_vector": vector[-30:], "quote_updated_at": f"{str(rows[-1]['trade_date'])[:4]}-{str(rows[-1]['trade_date'])[4:6]}-{str(rows[-1]['trade_date'])[6:]} 15:00:00",
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
                        row_date = str(row["trade_date"])
                        vector = [entry for entry in vector if entry.get("date") != row_date]
                        existing_date = str(item.get("as_of_trade_date") or "")
                        pct_chg = row.get("pct_chg")
                        if existing_date == row_date and pct_chg is not None:
                            vector.append({"date": row_date, "return": float(pct_chg)})
                        elif previous_close and float(previous_close) > 0:
                            vector.append(self._return_entry(row_date, float(previous_close), float(close)))
                        previous_close = float(close)
                        item["as_of_trade_date"] = row_date
                    if previous_close:
                        item["latest_close"] = previous_close
                    item["index_return_vector"] = vector[-30:]
                    item["data_stage"] = data_stage
                    item["quote_updated_at"] = f"{str(item.get('as_of_trade_date') or latest)[:4]}-{str(item.get('as_of_trade_date') or latest)[4:6]}-{str(item.get('as_of_trade_date') or latest)[6:]} 15:00:00"
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
                    item["data_stage"] = data_stage
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
                return {"updated": True, "tradeDate": data_trade_date, "stockCount": len(stocks), "calculationCount": len(calculations), "historyRebuilt": rebuild_history, "historyWindow": len(needed_dates) if rebuild_history else None}
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
            stages = [str(item.get("data_stage") or "") for item in rows if str(item.get("as_of_trade_date") or "") == latest]
            stage = "formal" if "formal" in stages else (stages[0] if stages else "")
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
                if self._date_key(self._quote_date_string(quote)) != today:
                    continue
                vector = self._apply_quote_to_vector(self._vector_entries(item.get("index_return_vector")), item.get("latest_close"), quote)
                item.update({"index_code": code, "index_name": item.get("index_name") or code, "latest_close": float(quote["current"]), "as_of_trade_date": today, "index_return_vector": vector[-30:], "data_stage": "initial", "quote_updated_at": str(quote.get("updatedAt") or "").replace("T", " ")[:19]})
                indexes.append(item)
            index_map = {item["index_code"]: item for item in indexes}
            calculations: List[Dict[str, Any]] = []
            for stock in stocks:
                item = existing.get(stock["ts_code"])
                quote = quotes.get(stock["ts_code"].upper())
                index_item = index_map.get(_index_code_for_stock(stock))
                index_quote = quotes.get(_index_code_for_stock(stock).upper())
                if (
                    not item
                    or not index_item
                    or self._date_key(self._quote_date_string(quote)) != today
                    or not self._quotes_share_snapshot(quote, index_quote)
                ):
                    continue
                quote = dict(quote)
                vector = self._apply_quote_to_vector(self._vector_entries(item.get("stock_return_vector")), item.get("latest_close"), quote)
                item = dict(item)
                item.update({"ts_code": stock["ts_code"], "latest_close": float(quote["current"]), "as_of_trade_date": today, "stock_return_vector": vector[-30:], "data_stage": "initial", "quote_updated_at": str(quote.get("updatedAt") or "").replace("T", " ")[:19]})
                metrics = self._vector_metrics(vector, self._vector_entries(index_item.get("index_return_vector")), BOARD_RULES[BOARD_BY_MARKET.get(stock["board"], Board.MAIN)])
                item.update({"deviation3": metrics["deviations"][3], "deviation10": metrics["deviations"][10], "deviation30": metrics["deviations"][30], "sameDirectionUp": metrics["up"], "sameDirectionDown": metrics["down"]})
                calculations.append(item)
            if not calculations or not indexes:
                return {"updated": False, "reason": "初态行情源未返回完整股票和指数数据"}
            self._calc_repository.upsert_market(stocks, calculations, indexes, today, data_stage="initial")
            self._stocks_cache = (now, stocks)
            return {"updated": True, "tradeDate": today, "dataStage": "initial", "calculationCount": len(calculations)}

    def _generate_prediction_datasets(self, data_stage: str) -> None:
        """基础数据阶段完成后，按目标交易日归档 today/next_day 两个结果集。"""
        base_trade_date = self._latest_data_stage_date()[0] or self._confirmed_market_trade_date()
        if not base_trade_date:
            return
        try:
            base_day = datetime.strptime(base_trade_date, "%Y%m%d").date()
            calendar = self.client.trade_cal(
                (base_day - timedelta(days=3)).strftime("%Y%m%d"),
                (base_day + timedelta(days=15)).strftime("%Y%m%d"),
            )
            open_dates = sorted(str(row.get("cal_date")) for row in calendar.to_dict("records") if int(row.get("is_open", 0)) == 1)
            next_trade_date = next((value for value in open_dates if value > base_trade_date), "")
        except Exception:
            next_trade_date = ""
        for scope in ("today", "next_day"):
            target_trade_date = base_trade_date if scope == "today" else next_trade_date
            try:
                items = self._compute_prediction_from_repository(scope, use_realtime=False, target_trade_date=target_trade_date) or []
                for item in items:
                    item["targetTradeDate"] = target_trade_date
                    item["scope"] = "当日" if scope == "today" else "次日"
                    item["predictionKey"] = f"{item.get('ts_code', '')}-{item.get('ruleKey', '')}-{target_trade_date}"
                updated_at = self._now()
                self._prediction_cache[scope] = (updated_at, items)
                self._prediction_trade_date[scope] = target_trade_date
                if self._calc_repository.available:
                    calculations = self._calc_repository.all_calculations() or {}
                    indexes = self._calc_repository.all_indexes() or {}
                    quote_times = [str(row.get("quote_updated_at") or "") for row in list(calculations.values()) + list(indexes.values()) if row.get("quote_updated_at")]
                    dataset_quote_updated_at = max(quote_times, default="")
                    # The tables retain historical rows for stocks that are no
                    # longer in the active stock pool. Completeness is about
                    # the rows that can participate in this dataset, not those
                    # orphaned rows. Mismatched active rows remain excluded by
                    # _compute_prediction_from_repository and are counted in
                    # exclusionStats.
                    stocks_by_code = {item["ts_code"]: item for item in self._stock_rows()}
                    valid_calculations = {
                        code: row for code, row in calculations.items()
                        if code in stocks_by_code
                        and self._date_key(row.get("as_of_trade_date")) == base_trade_date
                        and self._vector_latest_date(self._vector_entries(row.get("stock_return_vector"))) == base_trade_date
                    }
                    required_indexes = {
                        _index_code_for_stock(stock)
                        for stock in stocks_by_code.values()
                        if stock.get("ts_code") in valid_calculations
                    }
                    valid_indexes = {
                        code: row for code, row in indexes.items()
                        if code in required_indexes
                        and self._date_key(row.get("as_of_trade_date")) == base_trade_date
                        and self._vector_latest_date(self._vector_entries(row.get("index_return_vector"))) == base_trade_date
                    }
                    snapshot_complete = bool(valid_calculations and required_indexes) and required_indexes.issubset(valid_indexes)
                    self._calc_repository.save_prediction(
                        scope,
                        base_trade_date,
                        items,
                        updated_at,
                        data_stage=data_stage,
                        data_quality="provisional" if data_stage == "initial" else "confirmed",
                        rule_version=PREDICTION_RULE_VERSION,
                        target_trade_date=target_trade_date,
                        base_trade_date=base_trade_date,
                        dataset_quote_updated_at=dataset_quote_updated_at,
                        dataset_quote_as_of=base_trade_date,
                        dataset_quote_source="tushare" if data_stage == "formal" else "intraday-initial",
                        dataset_quote_complete=snapshot_complete,
                        exclusion_stats=self._prediction_generation_stats.get(scope, {}),
                        status="ready" if snapshot_complete else "incomplete",
                    )
            except Exception as exc:  # noqa: BLE001 - 保留上一份成功数据并记录任务失败
                self._prediction_last_error[scope] = str(exc)
                if self._calc_repository.available:
                    self._calc_repository.fail_prediction(scope, str(exc))

    def detail(self, ts_code: str) -> Dict[str, Any]:
        """返回单股当前状态、四项预警和模拟计算基础数据。"""

        if not self._calc_repository.available:
            raise CalculationDataUnavailable("MySQL 计算数据仓库不可用，单股计算已停止")
        try:
            stored = self._calc_repository.get_calc(str(ts_code or "").strip().upper())
        except Exception as exc:  # noqa: BLE001 - 不允许静默切换到另一套历史数据
            self._market_update_error = f"MySQL 读取失败：{exc}"
            raise CalculationDataUnavailable("MySQL 计算数据读取失败，单股计算已停止") from exc
        if not stored:
            raise CalculationDataUnavailable("数据库中缺少该股票的基础资料或历史计算基线，单股计算已停止")
        stock = self._normalise_stock_row(stored)
        if "stock_return_vector" not in stored or (
            not stored.get("stock_return_vector") and not stored.get("as_of_trade_date")
        ):
            raise CalculationDataUnavailable("数据库中缺少该股票的历史收益向量，单股计算已停止")
        try:
            result = self._detail_from_repository(stock, stored)
        except Exception as exc:  # noqa: BLE001 - 不允许静默切换到另一套历史数据
            self._market_update_error = f"MySQL 读取失败：{exc}"
            raise CalculationDataUnavailable("MySQL 计算数据读取失败，单股计算已停止") from exc
        if not result:
            raise CalculationDataUnavailable("数据库中的股票或指数基线缺失/日期不一致，单股计算已停止")
        return result

    def _compute_prediction_from_repository(self, scope: str, use_realtime: bool = False, target_trade_date: str = "") -> Optional[List[Dict[str, Any]]]:
        """从统一行情规则计算普通、同向和严重异动预测卡。"""

        calculations = self._calc_repository.all_calculations()
        indexes = self._calc_repository.all_indexes()
        if calculations is None or indexes is None or not calculations or not indexes:
            return None
        stocks = {item["ts_code"]: item for item in self._stock_rows()}
        monitored_codes = self._monitored_rule_codes()
        stats = {"excludedByTriggered": 0, "excludedByMonitor": 0, "excludedBySpecialStock": 0,
                 "excludedByDataQuality": 0, "excludedByLimit": 0}
        self._prediction_generation_stats[scope] = stats
        candidates: List[Dict[str, Any]] = []
        for ts_code, calc in calculations.items():
            stock = stocks.get(ts_code)
            if not stock:
                continue
            if not self._prediction_stock_eligible(stock, target_trade_date):
                stats["excludedBySpecialStock"] += 1
                continue
            index_code = _index_code_for_stock(stock)
            index = indexes.get(index_code)
            if not index:
                stats["excludedByDataQuality"] += 1
                continue
            board_rule = BOARD_RULES[BOARD_BY_MARKET.get(stock["board"], Board.MAIN)]
            stock_vector, index_vector, suspension, monitoring = self._rule_vectors_for_stock(
                stock,
                self._vector_entries(calc.get("stock_return_vector")),
                self._vector_entries(index.get("index_return_vector")),
            )
            stock_as_of = self._date_key(calc.get("as_of_trade_date"))
            index_as_of = self._date_key(index.get("as_of_trade_date"))
            if (
                suspension.get("suspended")
                or not stock_as_of
                or stock_as_of != index_as_of
                or self._vector_latest_date(stock_vector) != stock_as_of
                or self._vector_latest_date(index_vector) != index_as_of
            ):
                stats["excludedByDataQuality"] += 1
                continue
            metrics = self._vector_metrics(stock_vector, index_vector, board_rule)
            limit = float(board_rule.limit_price_ratio or 10.0)
            # 次日预测只面向“尚未进入当日严重异动”的股票。若任一严重规则
            # 已在当前快照触发，后续监管由重点监控承接，不再为同一股票生成
            # 另一严重窗口的次日候选（例如善水的 10 日已触发时不再展示 30 日卡）。
            already_triggered = bool(
                (
                    metrics["deviations"].get(10) is not None
                    and board_rule.severe_10d_threshold.triggered(metrics["deviations"][10])
                )
                or (
                    metrics["deviations"].get(30) is not None
                    and board_rule.severe_30d_threshold.triggered(metrics["deviations"][30])
                )
                or int(metrics.get("same", 0) or 0) >= board_rule.severe_same_direction_count
            )
            if scope == "next_day" and already_triggered:
                stats["excludedByTriggered"] += 1
                continue
            # 普通/同向短窗口卡只对已进入 10 日或 30 日监管期的股票开放。
            same_direction_count = int(metrics.get("same", 0) or 0)
            stock_keys = {str(stock.get("symbol") or ""), ts_code.split(".")[0]}
            severe_monitored = bool(stock_keys & monitored_codes.get("__severe_monitor__", set()))
            rules = [
                ("severe_10d", 10, board_rule.severe_10d_threshold.up, "deviation", board_rule.severe_10d_threshold.up),
                ("severe_30d", 30, board_rule.severe_30d_threshold.up, "deviation", board_rule.severe_30d_threshold.up),
            ]
            if severe_monitored:
                rules.insert(0, ("ordinary_3d", 3, board_rule.ordinary_deviation, "deviation", board_rule.ordinary_deviation))
            rule_options: List[Dict[str, Any]] = []
            for rule_key, rule_window, threshold, kind, target in rules:
                if scope in {"today", "next_day"} and (stock.get("symbol") in monitored_codes.get(rule_key, set()) or ts_code.split(".")[0] in monitored_codes.get(rule_key, set())):
                    stats["excludedByMonitor"] += 1
                    continue
                deviation = metrics["deviations"].get(rule_window)
                actual_window = metrics.get("window_lengths", {}).get(rule_window)
                if deviation is None or actual_window is None or deviation <= 0:
                    stats["excludedByDataQuality"] += 1
                    continue
                distance = self._required_up_percent(
                    stock_vector,
                    index_vector,
                    actual_window,
                    target,
                    forecast_scope=scope,
                )
                if distance is None:
                    stats["excludedByDataQuality"] += 1
                    continue
                triggered = (same_direction_count >= board_rule.severe_same_direction_count) if kind == "same_direction" else deviation >= threshold
                if scope == "next_day" and triggered:
                    stats["excludedByTriggered"] += 1
                    continue
                if not triggered and distance > limit:
                    stats["excludedByLimit"] += 1
                    continue
                rule_options.append({"ruleKey": rule_key, "ruleWindow": rule_window, "actualWindow": actual_window, "deviation": deviation, "threshold": threshold, "distance": distance, "kind": kind, "triggered": triggered})

            for option in rule_options:
                triggered = bool(option["triggered"])
                kind = option["kind"]
                deviation = option.get("deviation")
                deviation_label = f"{option['actualWindow']}日 {_pct(deviation)}"
                # 卡片展示下一交易日达到规则所需的上涨距离；规则阈值保留在 rule 文案。
                display_distance = float(option["threshold"]) if triggered else max(0.0, float(option["distance"]))
                trigger_value = f"+{display_distance:.2f}%"
                trigger_condition = f"涨幅达 {display_distance:.2f}%"
                if option["ruleKey"] == "ordinary_3d":
                    rule_text = f"连续3个交易日内收盘价涨幅偏离值累计达到 +{option['threshold']:.0f}%"
                elif option["ruleKey"] == "severe_30d":
                    rule_text = f"连续30个交易日内收盘价涨幅偏离值累计达到 +{option['threshold']:.0f}%"
                else:
                    rule_text = f"连续10个交易日内收盘价涨幅偏离值累计达到 +{option['threshold']:.0f}%"
                candidate = {
                    **stock,
                    "predictionKey": f"{ts_code}-{option['ruleKey']}-{str(calc.get('as_of_trade_date') or '')}",
                    "ruleKey": option["ruleKey"],
                    "predictionWindow": option["ruleWindow"],
                    "scope": "当日" if scope == "today" else "次日",
                    "targetTradeDate": "",
                    "currentPrice": f"{float(calc.get('latest_close')):.2f}" if calc.get("latest_close") is not None else "--",
                    "change": _pct(stock_vector[-1].get("return") if stock_vector else None),
                    "changeSource": str(calc.get("data_stage") or "formal"),
                    "deviation": deviation_label,
                    "deviationLabel": deviation_label,
                    "deviationPercent": float(deviation),
                    "deviationWindow": option["actualWindow"],
                    "trigger": trigger_condition,
                    "triggerCondition": trigger_condition,
                    "triggerValue": trigger_value,
                    "rule": rule_text,
                    "tradeDate": str(calc.get("as_of_trade_date") or ""),
                    "quoteUpdatedAt": self._format_prediction_datetime(calc.get("quote_updated_at")) or (
                        f"{str(calc.get('as_of_trade_date') or '')[:4]}-{str(calc.get('as_of_trade_date') or '')[4:6]}-{str(calc.get('as_of_trade_date') or '')[6:]} 15:00"
                        if str(calc.get("as_of_trade_date") or "") else ""
                    ),
                    "suspensionResetDate": suspension.get("resetDate", ""),
                    "ruleResetDate": suspension.get("ruleResetDate", ""),
                    "triggered": triggered,
                    "alertText": "已触发" if triggered else "",
                    "cardTone": "triggered" if triggered else "",
                    "_stockVector": stock_vector,
                    "_indexVector": index_vector,
                    "_indexCode": index_code,
                    "_indexLatestClose": index.get("latest_close"),
                    "_deviation": deviation,
                    "_window": option["actualWindow"],
                    "_rule_window": option["ruleWindow"],
                    "_threshold": option["threshold"],
                    "_trigger_kind": kind,
                }
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
                if self._quotes_share_snapshot(quote, index_quote):
                    threshold = float(item["_threshold"])
                    rule_window = int(item.get("_rule_window", item["_window"]))
                    deviation, actual_window = self._best_vector_deviation(stock_vector, index_vector, rule_window)
                    if deviation is not None:
                        item["_deviation"] = deviation
                        item["_window"] = actual_window or item["_window"]
                        item["deviation"] = f"{item['_window']}日 {_pct(deviation)}"
                        item["deviationLabel"] = item["deviation"]
                        item["deviationPercent"] = float(deviation)
                        item["triggered"] = deviation >= threshold
                        distance = self._required_up_percent(
                            stock_vector,
                            index_vector,
                            actual_window or item["_window"],
                            threshold,
                            forecast_scope=scope,
                        )
                        display_distance = threshold if item["triggered"] else max(0.0, float(distance or 0.0))
                        item["triggerValue"] = f"+{display_distance:.2f}%"
                        item["triggerCondition"] = f"涨幅达 {display_distance:.2f}%"
                        item["trigger"] = item["triggerCondition"]
                    if deviation is not None:
                        item["alertText"] = "已触发" if item["triggered"] else ""
                        item["cardTone"] = "triggered" if item["triggered"] else ""
                    item["currentPrice"] = f"{float(quote.get('current')):.2f}"
                    item["change"] = _pct(quote.get("pctChg"))
                    item["changeSource"] = "realtime"
                    item["quoteUpdatedAt"] = quote.get("updatedAt", "")
                    item["quoteSource"] = quote.get("source", "腾讯行情")
                updated.append(item)
            candidates = updated
        for item in candidates:
            for key in ("_stockVector", "_indexVector", "_indexCode", "_indexLatestClose", "_deviation", "_window", "_rule_window", "_threshold", "_trigger_kind"):
                item.pop(key, None)
            item.pop("currentPrice", None)
        return self._sort_prediction_items(candidates)

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
                if deviation < target_up and distance > float(board_rule.limit_price_ratio or 10.0):
                    continue
            elif not (0 < distance <= float(board_rule.limit_price_ratio or 10.0)):
                continue
            last = sorted(rows, key=lambda item: str(item["trade_date"]))[-1]
            result.append({
                **stock,
                "scope": "当日" if scope == "today" else "次日",
                "currentPrice": f"{float(last.get('close')):.2f}" if last.get("close") is not None else "--",
                "change": _pct(last.get("pct_chg")),
                "trigger": f"{window}日偏离达到 +{target_up:.2f}%",
                "deviation": f"{window}日 {_pct(deviation)}",
                "deviationLabel": f"{window}日 {_pct(deviation)}",
                "triggerCondition": f"{window}日偏离达到 +{target_up:.2f}%",
                "triggerValue": f"+{target_up:.2f}%",
                "rule": f"连续{window}个交易日内偏离值达到 +{target_up:.0f}%",
                "tradeDate": latest,
                "triggered": bool(deviation >= target_up),
                "alertText": "⚠️已触发" if deviation >= target_up else "",
                "cardTone": "triggered" if deviation >= target_up else "",
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
        output = result
        # 更新时间以扫描完成为准，避免把后台计算耗时误算进快照年龄。
        self._prediction_cache[scope] = (self._now(), output)
        self._prediction_trade_date[scope] = latest
        return output

    def _prediction_state(self) -> Dict[str, Any]:
        """按上海交易日和时钟决定预测页的显示状态。

        00:00-09:00 属于盘前；09:00-09:14 等待当日数据集切换；
        09:15-15:00 允许用户刷新实时行情；15:00 后等待后台初态/正式数据。
        """

        now = self._now()
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
        if not is_trade_day:
            phase = "rest_day"
        elif now.hour < 9:
            phase = "pre_open"
        elif now.hour == 9 and now.minute < 15:
            phase = "dataset_open_pending"
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
            # 09:00 切换前继续保留上一交易日收盘的 today（包括已触发卡）；
            # 09:00 后上一轮 next_day 才成为当前 today。
            "todaySourceScope": "next_day" if phase in ("dataset_open_pending", "intraday") else "today",
            "realtimeRefreshAllowed": phase == "intraday",
        }
        self._prediction_state_cache = (now, state)
        return state

    @staticmethod
    def _next_day_dataset_available(
        phase: str,
        state: Dict[str, Any],
        dataset_date: str,
        has_dataset: bool,
        data_stage: str = "formal",
    ) -> bool:
        """判断次日页是否可见，不把“有数据”与“当前阶段”混为一谈。

        下一交易日 09:00 前展示已生成的下一个交易日数据集；09:00
        切换后该数据集成为 today，交易时间内不显示尚未生成的新次日集。
        收盘待确认期间若已有下一交易日初态数据则开放；正式收盘确认后
        必须使用下一交易日正式数据集。
        """

        if not has_dataset or not dataset_date:
            return False
        if phase in ("rest_day", "pre_open"):
            return data_stage in ("initial", "formal") and dataset_date == str(state.get("targetTradeDate") or "")
        if phase in ("dataset_open_pending", "intraday"):
            return False
        if phase == "post_close_pending":
            return data_stage == "initial" and dataset_date == str(state.get("nextTradeDate") or "")
        if phase == "post_close_confirmed":
            return data_stage == "formal" and dataset_date == str(state.get("nextTradeDate") or "")
        return False

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
    def _parse_prediction_datetime(value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("T", " "))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
        return parsed.astimezone(SHANGHAI_TZ)

    @staticmethod
    def _normalize_prediction_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """只补齐旧数据缺失的安全默认值，不改写触发条件。"""

        normalized = dict(item or {})
        if normalized.get("deviationLabel") is None and normalized.get("deviation"):
            normalized["deviationLabel"] = normalized.get("deviation")
        normalized.setdefault("triggerValue", "--")
        normalized.setdefault("triggerCondition", normalized.get("trigger", ""))
        normalized.setdefault("alertText", "已触发" if normalized.get("triggered") else "")
        normalized.setdefault("cardTone", "triggered" if normalized.get("triggered") else "")
        return normalized

    @classmethod
    def _normalize_prediction_items(cls, items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [cls._normalize_prediction_item(item) for item in (items or [])]

    @staticmethod
    def _merge_prediction_items(primary: Iterable[Dict[str, Any]], secondary: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """合并同一展示日的已触发卡和临界卡，按窗口键去重。"""
        merged: Dict[str, Dict[str, Any]] = {}
        for item in list(primary or []) + list(secondary or []):
            item = MarketService._normalize_prediction_item(item)
            key = item.get("predictionKey") or f"{item.get('ts_code', '')}-{item.get('ruleKey', item.get('predictionWindow', ''))}-{item.get('targetTradeDate', '')}"
            if key not in merged:
                merged[key] = item
        return list(merged.values())

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
            job: Optional[Dict[str, Any]] = None
            if self._calc_repository.available:
                try:
                    record = self._prediction_record(scope)
                    status_reader = getattr(self._calc_repository, "prediction_job_status", None)
                    if status_reader:
                        job = status_reader(scope)
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
                "tradeDate": str((record or {}).get("base_trade_date") or (record or {}).get("trade_date") or self._prediction_trade_date.get(scope, "")),
                "targetTradeDate": str((record or {}).get("target_trade_date") or ""),
                "updatedAt": self._format_prediction_datetime(updated),
                "ageSeconds": age_seconds,
                # 节假日允许沿用最近成功数据，不把自然日年龄误报成故障。
                "stale": bool(age_seconds is None or (age_seconds > 36 * 3600 and today_is_trade_day is not False)),
                "itemCount": len(items or []),
                "datasetQuoteUpdatedAt": self._format_prediction_datetime((record or {}).get("dataset_quote_updated_at")),
                "datasetQuoteComplete": bool((record or {}).get("dataset_quote_complete", False)),
                "exclusionStats": (record or {}).get("exclusion_stats") or self._prediction_generation_stats.get(scope, {}),
                "status": str(
                    "failed" if (job or {}).get("status") == "failed" else
                    (record or {}).get("status") or ("ready" if record else "missing")
                ),
                "dataStage": str((record or {}).get("data_stage") or "formal"),
                "dataQuality": str((record or {}).get("data_quality") or "confirmed"),
                "ruleVersion": str((record or {}).get("rule_version") or PREDICTION_RULE_VERSION),
                "lastError": str(
                    (job or {}).get("last_error")
                    or (record or {}).get("last_error")
                    or self._prediction_last_error.get(scope, "")
                )[:160],
            }
        return result

    def predictions(self, scope: str, force: bool = False) -> Dict[str, Any]:
        if scope not in ("today", "next_day"):
            raise ValueError("scope 必须是 today 或 next_day")
        now = self._now()
        state = self._prediction_state()
        source_scope = state["todaySourceScope"] if scope == "today" else "next_day"
        lookup_date = str(state.get("targetTradeDate") or "")
        if scope == "next_day" and state["phase"] in ("post_close_pending", "post_close_confirmed"):
            lookup_date = str(state.get("nextTradeDate") or "")
        if source_scope == "today" and state.get("phase") in ("rest_day", "pre_open"):
            lookup_date = str(state.get("previousTradeDate") or lookup_date)
        persistent = None
        prediction_job: Optional[Dict[str, Any]] = None
        if self._calc_repository.available:
            try:
                persistent = self._prediction_record(source_scope, lookup_date)
                status_reader = getattr(self._calc_repository, "prediction_job_status", None)
                if status_reader:
                    prediction_job = status_reader(source_scope)
            except Exception as exc:
                self._prediction_last_error[source_scope] = str(exc)
        if persistent:
            updated = persistent.get("updated_at")
            updated_dt = updated.replace(tzinfo=SHANGHAI_TZ) if isinstance(updated, datetime) and updated.tzinfo is None else updated
            if isinstance(updated_dt, datetime) and persistent.get("status") != "failed":
                self._prediction_cache[source_scope] = (updated_dt, persistent.get("items") or [])
            self._prediction_trade_date[source_scope] = str(persistent.get("target_trade_date") or persistent.get("trade_date") or "")
            if persistent.get("last_error"):
                self._prediction_last_error[source_scope] = str(persistent.get("last_error"))
        dataset_date = str((persistent or {}).get("target_trade_date") or self._prediction_trade_date.get(source_scope, ""))
        # 接口顶层日期必须对应本次实际展示的数据集。休息日/盘前的 today
        # 展示上一交易日，不能把阶段状态里的下一交易日误报给前端。
        display_target_date = dataset_date or lookup_date
        cached = self._prediction_cache.get(source_scope)
        # GET 只读已生成的数据集；盘中实时结果由 POST refresh 直接返回，不进入缓存或数据库。
        items = list(cached[1] if cached else [])
        target_date = str(state.get("targetTradeDate") or "")
        dataset_matches_target = dataset_date == target_date
        next_data_stage = str((persistent or {}).get("data_stage") or "formal")
        next_record = persistent if scope == "next_day" else None
        next_dataset_date = lookup_date if scope == "next_day" else str(state.get("targetTradeDate") or "")
        if scope != "next_day" and state["phase"] in ("post_close_pending", "post_close_confirmed"):
            next_dataset_date = str(state.get("nextTradeDate") or "")
        if scope != "next_day" and self._calc_repository.available and next_dataset_date:
            try:
                next_record = self._prediction_record("next_day", next_dataset_date)
            except Exception as exc:
                self._prediction_last_error["next_day"] = str(exc)
        next_trade_available = bool(
            next_record
            and self._next_day_dataset_available(
                state["phase"], state,
                str(next_record.get("target_trade_date") or ""),
                True,
                str(next_record.get("data_stage") or "formal"),
            )
        )
        if scope == "next_day" and not next_trade_available:
            items = []
        if scope == "today" and source_scope == "next_day" and not dataset_matches_target:
            items = []
        # 普通/同向卡只有在股票仍处于 10 日或 30 日重点监控期时才展示。
        monitored_codes = self._monitored_rule_codes()
        items = [item for item in items if self._prediction_item_allowed(item, monitored_codes, scope)]
        items = self._sort_prediction_items(self._normalize_prediction_items(items))
        # 交易时段 today 允许复用上一交易日的 next_day 候选池；对外展示范围
        # 仍必须是“当日”，不能把底层数据集名称泄漏到卡片字段。
        if scope == "today" and source_scope == "next_day":
            for item in items:
                item["scope"] = "当日"
        refreshing = False
        dataset_updated = self._format_prediction_datetime((persistent or {}).get("dataset_quote_updated_at")) or self._format_prediction_datetime((persistent or {}).get("updated_at") or (cached[0] if cached else None))
        has_realtime = False
        stored_stage = str((persistent or {}).get("data_stage") or "formal")
        quality = str((persistent or {}).get("data_quality") or ("provisional" if stored_stage == "initial" else "confirmed"))
        job_failed = str((prediction_job or {}).get("status") or "") == "failed"
        dataset_status = "failed" if job_failed else str((persistent or {}).get("status") or ("ready" if persistent else "missing"))
        dataset_error = str(
            (prediction_job or {}).get("last_error")
            or (persistent or {}).get("last_error")
            or self._prediction_last_error.get(source_scope, "")
        )
        dataset_incomplete = bool(persistent and (persistent.get("status") == "incomplete" or not persistent.get("dataset_quote_complete", True)))
        if dataset_incomplete:
            items = []
        return {
            "scope": scope,
            "items": items,
            "tradeDate": str((persistent or {}).get("base_trade_date") or (persistent or {}).get("trade_date") or self._prediction_trade_date.get(source_scope, "")),
            "baseTradeDate": str((persistent or {}).get("base_trade_date") or (persistent or {}).get("trade_date") or ""),
            "targetTradeDate": display_target_date,
            "previousTradeDate": state["previousTradeDate"],
            "nextTradeDate": state["nextTradeDate"],
            "phase": state["phase"],
            "todaySourceScope": state["todaySourceScope"],
            "realtimeRefreshAllowed": state["realtimeRefreshAllowed"],
            "nextDayAvailable": next_trade_available,
            "nextDayReason": (
                "交易时间内暂不开放次日预测" if state["phase"] in ("intraday", "dataset_open_pending") else
                "等待当日初态数据" if state["phase"] == "post_close_pending" else
                "次日预测数据暂不可用"
            ) if not next_trade_available else "",
            "quoteUpdatedAt": dataset_updated,
            "datasetQuoteUpdatedAt": self._format_prediction_datetime((persistent or {}).get("dataset_quote_updated_at")) or dataset_updated,
            "datasetQuoteComplete": bool((persistent or {}).get("dataset_quote_complete", True)),
            "exclusionStats": (persistent or {}).get("exclusion_stats") or self._prediction_generation_stats.get(source_scope, {}),
            # 任务失败时保留上一份 items，但明确告诉前端更新时间位置显示不可用。
            "dataUnavailable": bool(job_failed or (persistent or {}).get("status") in {"failed", "incomplete"} or dataset_incomplete or ((persistent is None) and not items)),
            "datasetStatus": dataset_status,
            "updatedAt": dataset_updated,
            "dataStage": stored_stage,
            "refreshStatus": "not_allowed",
            "refreshing": refreshing,
            "error": dataset_error,
            "dataQuality": {
                "source": "腾讯/新浪/东方财富" if has_realtime else ("Tushare" if stored_stage == "formal" else "腾讯/新浪/东方财富"),
                "intraday": False,
                "quality": quality,
                "stage": stored_stage,
                "complete": bool((persistent or {}).get("dataset_quote_complete", True)),
                # 质量字段保留给监控和兼容调用方；预测页不展示实时行情延迟提示。
                "message": "初态数据，待 Tushare 正式数据确认" if stored_stage == "initial" else "基于最近已确认收盘数据",
            },
        }

    def _refresh_existing_prediction_scope(self, source_scope: str, forecast_scope: str = "") -> List[Dict[str, Any]]:
        """只刷新已有预测池，不重新扫描全市场，也不保存盘中结果。"""
        forecast_scope = forecast_scope or source_scope
        persistent = self._calc_repository.get_prediction(source_scope) if self._calc_repository.available else None
        base_items = list(self._merge_prediction_items(
            (persistent or {}).get("items") or [],
            self._prediction_cache.get(source_scope, (self._now(), []))[1],
        ))
        # 旧数据集可能残留短窗口卡；只有当前仍在严重异动监管期才放行。
        base_items = [item for item in base_items if self._prediction_item_allowed(item, self._monitored_rule_codes(), source_scope)]
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
            paired_quote = self._quotes_share_snapshot(quote, index_quote)
            if paired_quote:
                item["currentPrice"] = f"{float(quote.get('current')):.2f}"
                item["change"] = _pct(quote.get("pctChg"))
                item["changeSource"] = "realtime"
                item["quoteUpdatedAt"] = quote.get("updatedAt", "")
                item["quoteSource"] = quote.get("source", "腾讯行情")
            # 只有股票和对应指数同时成功，才临时修正偏离值，避免混合时点。
            if paired_quote and calc and index:
                stock_vector = self._apply_quote_to_vector(self._vector_entries(calc.get("stock_return_vector")), calc.get("latest_close"), quote)
                index_vector = self._apply_quote_to_vector(self._vector_entries(index.get("index_return_vector")), index.get("latest_close"), index_quote)
                stock_vector, index_vector, suspension, _monitoring = self._rule_vectors_for_stock(
                    stock, stock_vector, index_vector
                )
                if suspension.get("suspended"):
                    continue
                rule = BOARD_RULES[BOARD_BY_MARKET.get(stock.get("board"), Board.MAIN)]
                metrics = self._vector_metrics(stock_vector, index_vector, rule)
                rule_key = str(item.get("ruleKey") or "")
                rule_window = {"ordinary_3d": 3, "same_direction_10d": 3, "severe_10d": 10, "severe_30d": 30}.get(rule_key, int(item.get("predictionWindow") or 10))
                deviation = metrics.get("deviations", {}).get(rule_window)
                actual_window = metrics.get("window_lengths", {}).get(rule_window) or rule_window
                # 盘中只修正已经生成的候选，不重新扫描全市场；但修正必须使用
                # 与后台生成、单股详情相同的有效窗口和复牌重置逻辑。
                if deviation is not None:
                    threshold = rule.ordinary_deviation if rule_key in {"ordinary_3d", "same_direction_10d"} else (rule.severe_30d_threshold.up if rule_key == "severe_30d" else rule.severe_10d_threshold.up)
                    distance = self._required_up_percent(
                        stock_vector,
                        index_vector,
                        actual_window,
                        threshold,
                        forecast_scope=forecast_scope,
                    )
                    item["deviation"] = f"{actual_window}日 {_pct(deviation)}"
                    item["deviationLabel"] = item["deviation"]
                    item["deviationPercent"] = float(deviation)
                    item["triggered"] = (int(metrics.get("same", 0) or 0) >= rule.severe_same_direction_count) if rule_key == "same_direction_10d" else deviation >= threshold
                    if distance is not None:
                        item["triggerValue"] = f"+{max(0.0, float(distance)):.2f}%"
                        item["triggerCondition"] = f"涨幅达 {max(0.0, float(distance)):.2f}%"
                        # 兼容旧调用方：盘中重算后旧字段也必须与实时条件一致。
                        item["trigger"] = item["triggerCondition"]
                if deviation is not None:
                    item["alertText"] = "已触发" if item["triggered"] else ""
                    item["cardTone"] = "triggered" if item["triggered"] else ""
            if forecast_scope == "today":
                item["scope"] = "当日"
            item.pop("currentPrice", None)
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
        if state.get("realtimeRefreshAllowed"):
            # 盘中当日页映射到当前目标日的 next_day 数据集；只刷新该数据集已有卡片。
            source_scope = state["todaySourceScope"] if requested_scope == "today" else "next_day"
            with self._intraday_refresh_lock:
                future = self._intraday_refresh_futures.get(source_scope)
                if future is None or future.done():
                    future = self._prediction_executor.submit(
                        self._refresh_existing_prediction_scope,
                        source_scope,
                        requested_scope,
                    )
                    self._intraday_refresh_futures[source_scope] = future
            items: List[Dict[str, Any]] = []
            refresh_succeeded = False
            try:
                items = future.result(timeout=10)
                refresh_succeeded = True
                # 盘中结果只随本次响应返回，不写入 MySQL、Redis 或进程缓存。
            except Exception as exc:
                self._prediction_last_error[source_scope] = str(exc)
            if not refresh_succeeded:
                return self.predictions(requested_scope)
            result = self.predictions(requested_scope)
            result["items"] = self._sort_prediction_items(self._normalize_prediction_items(items))
            quote_times = [str(item.get("quoteUpdatedAt") or "") for item in items if item.get("quoteUpdatedAt")]
            quote_updated_at = max(quote_times, default="")
            result.update({
                "quoteUpdatedAt": quote_updated_at or result.get("quoteUpdatedAt", ""),
                "dataStage": "realtime",
                "refreshStatus": "applied",
                "dataQuality": {
                    **(result.get("dataQuality") or {}),
                    "source": "腾讯/新浪/东方财富",
                    "intraday": True,
                    "quality": "realtime",
                    "stage": "realtime",
                    "message": "",
                },
                "dataUnavailable": False,
                "refreshing": False,
            })
            return result
        return self.predictions(requested_scope)
