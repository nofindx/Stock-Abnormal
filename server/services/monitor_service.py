"""公开公告驱动的重点监控服务。

前端查询只读取 SQLite 中最近一次成功快照。公告采集、正文识别和快照替换只由后台任务执行，
不会因为用户打开页面或点击刷新而启动。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Event, Lock, Thread
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .announcement_service import AnnouncementService
from server.data.fact_ledger import FactLedger
from .tushare_client import TushareClient

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone(timedelta(hours=8))
# 风险提示和严重异动默认按 10 个交易日；退市整理按公告正文日期计算。
# 注意：30 日严重异动中的“30 个交易日”是规则判定的回看窗口，
# 不是公告触发后的监管期长度。规则计算窗口由 server/calculations/engine.py 负责。
MONITOR_PERIOD_TRADING_DAYS = {
    "ordinary": 10,       # 18.cn 交易所风险提示的默认监控窗口（起点计第 1 日）
    "severe-10d": 10,     # 10 日严重异动：触发后 10 个交易日
    "severe-30d": 10,     # 30 日偏离达到阈值后，仍按触发后 10 个交易日监管
    "delisting-warning": 10, # 正文缺少日期时的兼容周期
}
# 兼容外部测试/旧调用方；业务代码统一使用 MONITOR_PERIOD_TRADING_DAYS。
MONITOR_WINDOW_TRADING_DAYS = MONITOR_PERIOD_TRADING_DAYS
# 公告分类口径变化时，启动任务主动重采一次当前快照；避免必须等到次日
# 00:00 才让修复后的北交所严重异动进入重点监控。
MONITOR_RULE_VERSION = "2026-10-zts-effective-date-cninfo-official-ordinary-event-direction-fact-ledger-ocr-v27"

# 只有官方披露平台上的发行人/交易所事实，才有资格改变行情累计基线。
# 可读正文优先；普通异动正文短时不可读时，官方标题只能作为待行情核验
# 的事实入口。18.cn、中泰等风险提示可以作为监管事实或佐证，但不能单独触发重置。
RESET_AUTHORIZED_SOURCE_TYPES = {"issuer-disclosure", "exchange-disclosure"}
RESET_EVIDENCE_CODE = "official_severe_abnormality_body"
ORDINARY_RESET_EVIDENCE_CODE = "official_ordinary_abnormality_body"
# 巨潮正文短时不可读时，公告标题本身仍是官方事实证据。三日方向和
# 阈值由行情模块按公告日期核验；正文补齐后会以正文证据替换该记录。
ORDINARY_TITLE_EVIDENCE_CODE = "official_ordinary_abnormality_title_market"


class MonitorRepository:
    """用事务保存公告元数据和当前监控快照，不保存公告文件或正文。"""

    def __init__(self, path: Optional[str] = None) -> None:
        value = path or os.getenv("MONITOR_DB_PATH", "/tmp/stock-abnormal/monitor.db")
        self.path = Path(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        # 事实账本独立于当前快照，支持公告源部分失败后的重建与审计。
        self.fact_ledger = FactLedger(str(self.path.with_name(self.path.stem + ".facts.db")))
        self._init_schema()

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS announcement_raw (
                    source_id TEXT PRIMARY KEY, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL,
                    announcement_date TEXT NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL,
                    source_url TEXT NOT NULL, content_hash TEXT NOT NULL, confirmation_status TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, monitor_type TEXT NOT NULL DEFAULT '',
                    risk_tone TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT '',
                    source_relation TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS monitor_snapshot (
                    snapshot_id TEXT PRIMARY KEY, snapshot_date TEXT NOT NULL, updated_at TEXT NOT NULL,
                    status TEXT NOT NULL, source_status TEXT NOT NULL, item_count INTEGER NOT NULL,
                    payload TEXT NOT NULL, error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS monitor_state (
                    state_key TEXT PRIMARY KEY, state_value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ingest_run (
                    run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
                    status TEXT NOT NULL, item_count INTEGER NOT NULL, error_message TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(announcement_raw)").fetchall()}
            for name, definition in (
                ("monitor_type", "TEXT NOT NULL DEFAULT ''"),
                ("risk_tone", "TEXT NOT NULL DEFAULT ''"),
                ("source_type", "TEXT NOT NULL DEFAULT ''"),
                ("source_relation", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE announcement_raw ADD COLUMN {name} {definition}")

    def active_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._connect() as db:
            row = db.execute("""
                SELECT payload FROM monitor_snapshot
                WHERE status = 'success'
                ORDER BY updated_at DESC LIMIT 1
            """).fetchone()
        return json.loads(row["payload"]) if row else None

    def states(self) -> Dict[str, str]:
        with self._connect() as db:
            rows = db.execute("SELECT state_key, state_value FROM monitor_state").fetchall()
        return {str(row["state_key"]): str(row["state_value"]) for row in rows}

    def set_state(self, key: str, value: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES (?, ?)", (key, value))
            db.commit()

    def publish(self, snapshot: Dict[str, Any], announcements: List[Dict[str, Any]], run_id: str) -> None:
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        # 先写不可变事实账本；快照替换失败不影响后续重建。
        self.fact_ledger.upsert(announcements)
        self.fact_ledger.upsert_pending(snapshot.get("unresolvedFacts") or [])
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in announcements:
                db.execute("""
                    INSERT OR REPLACE INTO announcement_raw
                    (source_id, stock_code, stock_name, announcement_date, title, source, source_url,
                     content_hash, confirmation_status, fetched_at, monitor_type, risk_tone, source_type, source_relation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item["sourceId"], item["symbol"], item["name"], item["announcementDate"],
                    item["title"], item["source"], item["sourceUrl"], item["contentHash"],
                    item["confirmationStatus"], snapshot["updatedAt"],
                    item.get("monitorType", ""), item.get("riskTone", ""), item.get("sourceType", ""), item.get("sourceRelation", ""),
                ))
            db.execute("""
                INSERT INTO monitor_snapshot
                (snapshot_id, snapshot_date, updated_at, status, source_status, item_count, payload)
                VALUES (?, ?, ?, 'success', ?, ?, ?)
            """, (
                snapshot["snapshotId"], snapshot["snapshotDate"], snapshot["updatedAt"],
                snapshot["dataQuality"]["announcementStatus"], len(snapshot["items"]), payload,
            ))
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES ('active_snapshot_id', ?)", (snapshot["snapshotId"],))
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES ('last_status', 'success')")
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES ('last_failure_date', '')")
            # 只保留当前成功快照和当前来源元数据；不保留历史监控快照。
            db.execute("DELETE FROM monitor_snapshot WHERE snapshot_id != ?", (snapshot["snapshotId"],))
            source_ids = [item["sourceId"] for item in announcements if item.get("sourceId")]
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                db.execute(f"DELETE FROM announcement_raw WHERE source_id NOT IN ({placeholders})", source_ids)
            else:
                db.execute("DELETE FROM announcement_raw")
            db.execute("INSERT OR REPLACE INTO ingest_run VALUES (?, ?, ?, 'success', ?, '')", (
                run_id, snapshot["startedAt"], snapshot["updatedAt"], len(snapshot["items"]),
            ))
            db.execute("DELETE FROM ingest_run WHERE run_id != ?", (run_id,))
            db.commit()

    def rebuild_facts(self, symbol: Optional[str] = None, since: Optional[str] = None) -> List[Dict[str, Any]]:
        """从持久化账本重建公告事实，不依赖当前监控快照。"""
        return self.fact_ledger.rebuild(symbol=symbol, since=since)

    def record_failure(self, run_id: str, started_at: str, message: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO ingest_run VALUES (?, ?, ?, 'failed', 0, ?)", (
                run_id, started_at, datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M"), message,
            ))
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES ('last_status', 'failed')")
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES ('last_failure_date', ?)", (started_at[:10],))
            db.commit()


class OfficialMonitorService:
    """采集公开披露公告并生成重点监控快照，不读取全市场交易行情。"""

    def __init__(self, client: TushareClient, announcements: Optional[AnnouncementService] = None) -> None:
        self.client = client
        self.announcements = announcements or AnnouncementService()
        self.repository = MonitorRepository()
        # 只缓存当前严重异动期内且可直接下载的 PDF；默认使用容器临时盘，
        # 可通过 MONITOR_PDF_DIR 指向持久化卷。文件不进入 SQLite，也不写入小程序。
        self.pdf_dir = Path(os.getenv("MONITOR_PDF_DIR", "/tmp/stock-abnormal/monitor-pdf"))
        # Injectable clock keeps expiry and snapshot tests deterministic while
        # production continues to use Shanghai local time.
        self._clock = lambda: datetime.now(SHANGHAI_TZ)
        self.refreshing = False
        self.last_error = ""
        self._snapshot_published_callback = None
        self._lock = Lock()
        self._scheduler_stop = Event()
        self._scheduler: Optional[Thread] = None

    def start_scheduler(self) -> None:
        """启动后台定时器；HTTP 请求永远不会调用 refresh。"""

        if self._scheduler and self._scheduler.is_alive():
            return
        self._scheduler_stop.clear()
        self._scheduler = Thread(target=self._scheduler_loop, name="official-monitor", daemon=True)
        self._scheduler.start()

    def close(self) -> None:
        """停止后台任务，供测试和进程退出使用。"""

        self._scheduler_stop.set()

    def set_snapshot_published_callback(self, callback: Any) -> None:
        """Register a consumer to refresh derived data after new facts publish."""

        self._snapshot_published_callback = callback

    def health_stats(self, today_is_trade_day: Optional[bool] = None) -> Dict[str, Any]:
        """返回当前监管快照年龄和采集状态，不读取公告正文。"""

        snapshot = self.repository.active_snapshot()
        state = self.repository.states()
        updated_at = str((snapshot or {}).get("updatedAt") or "")
        age_seconds: Optional[int] = None
        if updated_at:
            try:
                parsed = datetime.fromisoformat(updated_at)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
                age_seconds = max(0, int((self._clock() - parsed.astimezone(SHANGHAI_TZ)).total_seconds()))
            except ValueError:
                age_seconds = None
        now = self._clock()
        today = now.strftime("%Y-%m-%d")
        active_items = [
            item for item in (snapshot or {}).get("items", [])
            if not self._is_expired(str(item.get("monitorEndDate") or ""), today)
        ]
        return {
            "available": bool(snapshot),
            "snapshotDate": str((snapshot or {}).get("snapshotDate") or ""),
            "updatedAt": updated_at,
            "ageSeconds": age_seconds,
            # 节假日不要求公告快照按自然日滚动更新。
            "stale": bool(age_seconds is None or (age_seconds > 36 * 3600 and today_is_trade_day is not False)),
            # 健康页展示当前有效监管条数，与 /api/monitor?status=current 一致；
            # 快照内部可能保留尚未清理的历史记录，不能直接计入当前数量。
            "itemCount": len(active_items),
            "dataQuality": (snapshot or {}).get("dataQuality") or {},
            "refreshing": bool(self.refreshing),
            "lastStatus": state.get("last_status", ""),
            "lastFailureDate": state.get("last_failure_date", ""),
            "lastError": self.last_error[:160] if self.last_error else "",
        }

    def _scheduler_loop(self) -> None:
        # 启动后先读当前快照；00:00 后每日首次采集，失败只在次日 01:00-09:00 每小时重试。
        self._scheduler_stop.wait(0.5)
        while not self._scheduler_stop.is_set():
            try:
                now = self._clock()
                today = now.strftime("%Y-%m-%d")
                state = self.repository.states()
                last_attempt_date = state.get("last_attempt_date", "")
                # 无当前快照时冷启动立即补采；有当前快照时每天 00:00 后只尝试一次。
                if state.get("monitor_rule_version") != MONITOR_RULE_VERSION or (
                    not self.repository.active_snapshot() and not last_attempt_date
                ) or (
                    now.hour == 0 and last_attempt_date != today
                ):
                    self.refresh()
                elif 1 <= now.hour <= 9:
                    # 只在失败次日 01:00-09:00 按小时重试；成功后不再重复采集。
                    failure_date = state.get("last_failure_date", "")
                    retry_date = (datetime.strptime(failure_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") if failure_date else ""
                    retry_key = f"retry_{today}_{now.hour:02d}"
                    if retry_date == today and state.get(retry_key) != "1" and state.get("last_status") == "failed":
                        self.refresh()
                        self.repository.set_state(retry_key, "1")
            except Exception as exc:  # noqa: BLE001 - 调度线程必须保持存活
                self.last_error = str(exc)
            self._scheduler_stop.wait(60)

    @staticmethod
    def _classify_monitor_text(title: str, body: str, symbol: str = "") -> tuple[str, str]:
        """按公告正文语义分类：30 日、10 日、风险提示；无法确认则丢弃。

        交易所公告经常把严重异动窗口写成“连续 6/8 个交易日”，而不是
        固定复述“10 个交易日”。只要正文明确写出严重异常、上涨偏离值
        累计达到 100% 以上，就归入产品的 10 日严重异动类型；3 日普通
        异动没有“严重异常波动”语义，仍不会被纳入。
        """

        # 分类严格基于正文；标题仅用于确认公告是否明确写明“严重异常波动”。
        text = re.sub(r"\s+", "", body or "").replace("％", "%")
        has_30_window = "30个交易日" in text
        has_10_window = "10个交易日" in text
        def deviation_values(window: str) -> List[float]:
            values: List[float] = []
            pattern = rf"(?:{window}个交易日.{{0,220}}?|连续\d+个交易日.{{0,220}}?)(?:涨幅|跌幅|偏离值).{{0,60}}?(?:达到|超过|为)([+-]?\d+(?:\.\d+)?)%"
            for match in re.finditer(pattern, text):
                try:
                    value = float(match.group(1))
                    fragment = text[max(0, match.start() - 24):match.end()]
                    if "跌幅" in fragment and value > 0:
                        value = -value
                    values.append(value)
                except ValueError:
                    continue
            return values

        values_30 = deviation_values("30")
        values_10 = deviation_values("10")
        code_match = re.search(r"\d{6}", str(symbol or ""))
        is_bse = bool(code_match and code_match.group(0).startswith(("4", "8", "9")))
        threshold_10 = 150 if is_bse else 100
        threshold_30 = 300 if is_bse else 200
        down_10 = -60 if is_bse else -50
        down_30 = -75 if is_bse else -70
        has_30_threshold = any(value >= threshold_30 or value <= down_30 for value in values_30)
        has_10_threshold = any(value >= threshold_10 or value <= down_10 for value in values_10)
        # 上海主板、创业板/科创板等规则还允许以连续交易日内多次
        # 同向异常波动认定为严重异常；这类公告可能不重复披露
        # “10 个交易日涨幅偏离值”句式，不能因此漏掉。
        same_direction_count = 0
        same_direction_window = 0
        same_match = re.search(r"连续(\d+)个交易日.{0,80}?出现(\d+)次同向异常波动", text)
        if same_match:
            same_direction_window = int(same_match.group(1))
            same_direction_count = int(same_match.group(2))
        code_value = code_match.group(0) if code_match else ""
        same_direction_threshold = 3 if (
            is_bse or code_value.startswith("3") or code_value.startswith("68")
        ) else 4
        has_same_direction_severe = (
            same_direction_window > 0
            and same_direction_count >= same_direction_threshold
        )
        if has_same_direction_severe:
            has_10_threshold = True
        has_risk_wording = OfficialMonitorService._has_exchange_strict_monitoring_wording(text)
        if has_30_window and has_30_threshold:
            return "30日严重异动", "severe-30d"
        if has_10_window and has_10_threshold:
            return "10日严重异动", "severe-10d"
        # 公告可能按实际连续交易日数披露本轮 10 日严重异动事实；方向阈值
        # 仍按该证券板块的 10 日规则核验。
        has_severe_title = "严重异常波动" in str(title or "") or "严重异常波动" in text
        has_threshold_deviation = any(value >= threshold_10 or value <= down_10 for value in deviation_values(""))
        has_actual_window = bool(re.search(r"连续\d+个交易日", text))
        if has_severe_title and has_actual_window and not has_30_window and (has_threshold_deviation or has_same_direction_severe):
            return "10日严重异动", "severe-10d"
        if has_risk_wording:
            return "风险提示", "ordinary"
        return "", ""

    @staticmethod
    def _severe_event_direction(body: str) -> str:
        """Return the direction explicitly stated for a severe-abnormality fact.

        Do not infer this from the latest daily return: a stock can fall today
        while remaining under an active, upward severe-abnormality notice.
        """
        text = re.sub(r"\s+", "", str(body or "")).replace("％", "%")
        if re.search(r"跌幅(?:偏离值)?|累计下跌|跌停|下跌幅度", text):
            return "down"
        if re.search(r"涨幅(?:偏离值)?|累计上涨|涨停|上涨幅度", text):
            return "up"
        return ""

    @staticmethod
    def _has_exchange_strict_monitoring_wording(text: str) -> bool:
        normalized = re.sub(r"\s+", "", text or "")
        exchange = r"(?:交易所|上交所|深交所|北交所)"
        subject = r"(?:以上证券|上述证券(?:上)?|该证券|该股票)"
        direct_confirmation = re.search(
            exchange + r"将对" + subject + r"的?异常交易行为进行(?:进一步)?从严认定",
            normalized,
        )
        listed_confirmation = re.search(
            exchange + r"将.{0,40}?列为重点监控证券.{0,100}?异常交易行为进行(?:进一步)?从严认定",
            normalized,
        )
        return bool(direct_confirmation or listed_confirmation)

    @staticmethod
    def _monitor_type(title: str, body: str) -> tuple[str, str]:
        return OfficialMonitorService._classify_monitor_text(title, body)

    @staticmethod
    def _issuer_monitor_type(title: str, body: str, symbol: str = "") -> tuple[str, str]:
        """分类发行人/交易所正文中的监管事实。

        发行人公告只有在正文明确出现交易所“进一步从严认定”语义时，
        才能成为普通风险提示。标题中的“风险提示”“复牌”“停牌核查”
        或正文中的一般交易风险表述都不能单独入选。
        """

        monitor_type, risk_tone = OfficialMonitorService._classify_monitor_text(title, body, symbol)
        if risk_tone:
            return monitor_type, risk_tone
        return ("风险提示", "ordinary") if OfficialMonitorService._has_exchange_strict_monitoring_wording(body) else ("", "")

    @staticmethod
    def _broker_monitor_type(title: str, body: str, symbol: str = "") -> tuple[str, str]:
        return OfficialMonitorService._classify_monitor_text(title, body, symbol)

    @staticmethod
    def _zts_monitor_type(title: str, body: str, hint: str = "") -> tuple[str, str]:
        if hint == "delisting-warning" or "退市整理" in str(title or "") or "退市风险警示" in str(title or ""):
            return "退市倒计时", "delisting-warning"
        if "交易风险提示" in str(title or "") and OfficialMonitorService._has_exchange_strict_monitoring_wording(body):
            return "风险提示", "ordinary"
        return ("风险提示", "ordinary") if OfficialMonitorService._has_exchange_strict_monitoring_wording(body) else ("", "")

    @staticmethod
    def _risk_prompt_relation(title: str, body: str, risk_tone: str) -> str:
        """标记风险提示与其他监管事实的关系，不用公告日期猜关系。

        `ordinary` 是独立的风险提示事实；只有正文明确复述“严重异常波动”
        或严重异动的窗口/偏离值时，才把它标记为同一轮严重异动的佐证来源。
        退市整理永远是独立类别，即使和其他公告同日也不能合并。
        """

        if risk_tone == "delisting-warning":
            return "delisting-independent"
        if risk_tone not in ("ordinary", "severe-10d", "severe-30d"):
            return ""
        if risk_tone.startswith("severe-"):
            return "severe-primary"
        text = re.sub(r"\s+", "", f"{title or ''}{body or ''}").replace("％", "%")
        severe_reference = (
            "严重异常波动" in text
            or "同向异常波动" in text
            or bool(re.search(r"(?:连续|近)\d+个交易日.{0,120}(?:涨幅|跌幅|偏离值)", text))
        )
        return "severe-supporting" if severe_reference else "ordinary-independent"

    @staticmethod
    def _reset_evidence(
        title: str,
        body: str,
        symbol: str,
        risk_tone: str,
        source_type: str,
        source_url: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Return auditable evidence for a rule-window reset.

        A title or a broker summary is never enough.  The body must be fetched
        from an authorized official disclosure source and independently pass the
        same severe-abnormality classifier used for the monitoring fact.
        """

        if risk_tone not in {"severe-10d", "severe-30d"}:
            return None
        if source_type not in RESET_AUTHORIZED_SOURCE_TYPES:
            return None
        host = (urlparse(str(source_url or "")).hostname or "").lower()
        official_host = any(
            host == domain or host.endswith(f".{domain}")
            for domain in ("cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn")
        )
        if not official_host:
            return None
        normalized = re.sub(r"\s+", "", str(body or "")).replace("％", "%")
        if not normalized or "严重异常波动" not in normalized:
            return None
        classified = OfficialMonitorService._classify_monitor_text(title, body, symbol)[1]
        if classified != risk_tone:
            return None
        # Keep only compact, deterministic evidence in the snapshot.  The full
        # announcement body is intentionally not persisted.
        return {
            "code": RESET_EVIDENCE_CODE,
            "sourceType": source_type,
            "authority": "巨潮资讯发行人/交易所正式披露正文",
            "sourceUrl": str(source_url),
            "bodyHash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "verifiedRiskTone": risk_tone,
            "signals": ["严重异常波动", "板块阈值或同向次数条件"],
        }

    @staticmethod
    def _ordinary_reset_evidence(
        title: str,
        body: str,
        symbol: str,
        source_type: str,
        source_url: str,
    ) -> Optional[Dict[str, Any]]:
        """Confirm an ordinary-abnormality reset from official evidence.

        A readable body is preferred. When the official PDF is temporarily
        unavailable, an unambiguous ordinary-abnormality title is retained and
        the market module must verify the dated three-session threshold.
        """
        if source_type not in RESET_AUTHORIZED_SOURCE_TYPES:
            return None
        host = (urlparse(str(source_url or "")).hostname or "").lower()
        official_host = any(
            host == domain or host.endswith(f".{domain}")
            for domain in ("cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn")
        )
        text = re.sub(r"\s+", "", str(body or "")).replace("％", "%")
        title_text = re.sub(r"\s+", "", str(title or ""))
        if (
            not official_host
            or "异常波动" not in title_text
            or "严重异常波动" in title_text
            or re.search(r"风险提示|风险警示|退市整理|停牌核查", title_text)
        ):
            return None
        # 标题已明确为普通《股票交易异常波动公告》时，正文暂时不可读
        # 也不能把这条官方事实丢掉。后续由行情模块用公告日期对应的三日
        # 股票/指数收盘数据确认阈值和方向；无法对齐时仍会显示待核验。
        if not text:
            return {
                "code": ORDINARY_TITLE_EVIDENCE_CODE,
                "sourceType": source_type,
                "authority": "巨潮资讯官方公告标题 + 对应三日收盘行情",
                "sourceUrl": str(source_url),
                "bodyHash": hashlib.sha256(title_text.encode("utf-8")).hexdigest(),
                "verifiedRiskTone": "ordinary-abnormality",
                "signals": ["官方标题确认普通异动公告", "方向和阈值待行情核验"],
                "countsForSameDirection": False,
                "countsForSameDirectionCandidate": True,
                "verificationMode": "title_market_pending_body",
            }
        normalized = text.replace("三个", "3个").replace("三個", "3个")
        normalized = normalized.replace("两个", "2个").replace("兩個", "2个").replace("两個", "2个")
        normalized = normalized.replace("五个", "5个").replace("五個", "5个")
        normalized = normalized.replace("累积换手率", "累计换手率")
        # 正式公告有时会在连续两日已经达到主板普通阈值时披露
        # “连续两个交易日”，这仍是可审计的普通异动事实；不能因不是
        # 三日文字而漏掉其后的重置边界。
        period_matches = list(re.finditer(r"连续([23])个交易日([^。；;]{0,260})", normalized))
        ordinary_claim = next(
            (match for match in period_matches if re.search(r"(?:偏离值|换手率)", match.group(2))),
            None,
        )
        if not ordinary_claim:
            # 官方标题已经确认这是普通异动公告；正文格式差异不应让事实
            # 消失。后续仍以公告日期对应的三日行情核验阈值和方向。
            return {
                "code": ORDINARY_TITLE_EVIDENCE_CODE,
                "sourceType": source_type,
                "authority": "巨潮资讯官方公告标题 + 对应三日收盘行情",
                "sourceUrl": str(source_url),
                "bodyHash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                "verifiedRiskTone": "ordinary-abnormality",
                "signals": ["官方标题确认普通异动公告", "正文格式未匹配，交由行情核验"],
                "countsForSameDirection": False,
                "countsForSameDirectionCandidate": True,
                "verificationMode": "title_market_body_format_fallback",
            }
        period_fragment = ordinary_claim.group(2)
        match = re.search(
            r"(?:偏离值累计|累计(?:涨幅|跌幅)?偏离值)"
            r"(?:达到|超过|高于|大于|不少于|为)?([±+-]?\d+(?:\.\d+)?)%",
            period_fragment,
        )
        code_match = re.search(r"\d{6}", str(symbol or ""))
        code = code_match.group(0) if code_match else ""
        is_bse = code.startswith(("4", "8", "9"))
        ordinary_threshold = 40 if is_bse else 30 if code.startswith(("3", "68")) else 20
        deviation_confirmed = False
        if match:
            try:
                deviation_confirmed = abs(float(match.group(1).replace("±", ""))) >= ordinary_threshold
            except ValueError:
                pass
        turnover_ratio_match = re.search(
            r"(?:日均)?换手率.{0,100}?(?:大于|达到|超过|高于|为)?([\d.]+)倍",
            period_fragment,
        )
        turnover_sum_match = re.search(
            r"累计换手率(?:达到|超过|高于|为)?([\d.]+)%",
            period_fragment,
        )
        turnover_confirmed = False
        if (
            not is_bse and not code.startswith(("3", "68"))
            and "日均换手率" in period_fragment
            and "前5个交易日" in period_fragment
            and turnover_ratio_match and turnover_sum_match
        ):
            try:
                turnover_confirmed = (
                    float(turnover_ratio_match.group(1)) >= 30
                    and float(turnover_sum_match.group(1)) >= 20
                )
            except ValueError:
                turnover_confirmed = False
        if not deviation_confirmed and not turnover_confirmed:
            return None
        return {
            "code": ORDINARY_RESET_EVIDENCE_CODE,
            "sourceType": source_type,
            "authority": "交易所/发行人正式异常波动公告正文",
            "sourceUrl": str(source_url),
            "bodyHash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "verifiedRiskTone": "ordinary-abnormality",
            "signals": [f"连续{ordinary_claim.group(1)}个交易日异常波动", "板块普通偏离阈值" if deviation_confirmed else "主板换手率条件"],
            "countsForSameDirection": bool(deviation_confirmed and ordinary_claim.group(1) == "3"),
        }

    @staticmethod
    def _ordinary_event_direction(body: str) -> str:
        """Extract direction only when the ordinary notice states it explicitly."""
        text = re.sub(r"\s+", "", str(body or ""))
        if re.search(r"(?<!涨)(?:累计)?跌幅偏离值|偏离值累计下跌|累计下跌幅度", text):
            return "down"
        if re.search(r"(?:累计)?涨幅偏离值|偏离值累计上涨|累计上涨幅度", text):
            return "up"
        return ""

    @staticmethod
    def _delisting_dates_from_body(body: str) -> tuple[str, str]:
        """Extract the announced start and last trading date of a delisting period."""

        text = str(body or "")

        def date_value(match: Any) -> str:
            year, month, day = (int(value) for value in match.groups())
            return f"{year:04d}-{month:02d}-{day:02d}"

        start_match = re.search(
            r"自\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s*起.{0,30}?进入退市整理期",
            text,
        )
        end_match = re.search(
            r"(?:预计)?最后交易日期?(?:为|是|将为|：|:)\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日",
            text,
        )
        return (
            date_value(start_match) if start_match else "",
            date_value(end_match) if end_match else "",
        )

    def _delisting_period_dates(self, body: str, announcement_date: str, symbol: str) -> tuple[List[str], str, str]:
        """Use the notice's effective entry and final-trading dates for countdowns."""

        start_date, end_date = self._delisting_dates_from_body(body)
        if not start_date or not end_date or start_date > end_date:
            dates = self._monitor_period_dates(announcement_date, "zts-risk-alert", "delisting-warning", symbol)
            return dates, dates[0] if dates else "", dates[-1] if dates else ""
        calendar_days = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days + 1
        dates = [value for value in self._trade_dates(start_date, calendar_days) if start_date <= value <= end_date]
        return dates, start_date, end_date

    @staticmethod
    def _is_accepted_broker_alert(title: str, body: str, symbol: str = "") -> bool:
        """18.cn 只接受正文能归类为风险提示或严重异动的记录。"""

        return bool(OfficialMonitorService._classify_monitor_text(title, body, symbol)[1])

    def _trade_dates(self, source_date: str, days: int) -> List[str]:
        """读取交易日历；数据源暂时不可用时退化为工作日，绝不把自然日冒充交易日。"""

        start = datetime.strptime(source_date, "%Y-%m-%d").date()
        end = start + timedelta(days=max(45, days * 3 + 30))
        try:
            frame = self.client.trade_cal(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            rows = frame.to_dict("records") if hasattr(frame, "to_dict") else frame
            dates = []
            for row in rows or []:
                if int(row.get("is_open", 0)) != 1:
                    continue
                value = str(row.get("cal_date", "")).replace("-", "")
                if len(value) == 8:
                    dates.append(f"{value[:4]}-{value[4:6]}-{value[6:8]}")
            if dates:
                return sorted(set(dates))
        except Exception:  # noqa: BLE001 - 交易日历降级不应阻断公告快照
            pass
        # 本地单测、Tushare 暂时不可用时只采用周一至周五，后续成功快照会用正式日历校正。
        return [
            (start + timedelta(days=offset)).strftime("%Y-%m-%d")
            for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        ]

    def _monitor_period(self, source_date: str, source_type: str, risk_tone: str = "ordinary", symbol: str = "") -> tuple[str, str]:
        """按附件口径计算监管期：起点取生效交易日，起点计第 1 日共 N 个交易日。"""

        dates = self._monitor_period_dates(source_date, source_type, risk_tone, symbol)
        if not dates:
            raise ValueError(f"无法计算监管期：{source_date}")
        return dates[0], dates[-1]

    def _monitor_period_dates(self, source_date: str, source_type: str, risk_tone: str = "ordinary", symbol: str = "", event_end_date: str = "") -> List[str]:
        """返回已冻结的监管交易日序列，供快照保存和倒计时使用。"""

        window = MONITOR_PERIOD_TRADING_DAYS.get(risk_tone, MONITOR_PERIOD_TRADING_DAYS["ordinary"])
        dates = self._trade_dates(source_date, window)
        if not dates:
            return []
        # 来源采用不同的生效日口径：18.cn 从公告后的下一交易日起算；
        # 中泰提示按其公告日期起算。严重公告正文明确事实截止日时，
        # 不论公告源，均从事实截止日后的首个交易日起算。
        is_bse = bool(re.match(r"^(?:4|8|9)\d{5}", str(symbol or "")))
        # 公告正文明确异动事实最后交易日时，监管从其后的首个交易日开始。
        # 这能处理公告发布日期晚于异动结束日的情况，例如近岸蛋白。
        # BSE severe notices are also published after the trigger session;
        # their period begins on the next trading session.
        starts_next_trade_day = source_type == "broker-risk-alert" or bool(event_end_date) or (is_bse and risk_tone in ("severe-10d", "severe-30d"))
        comparator = (lambda value: value > source_date) if starts_next_trade_day else (lambda value: value >= source_date)
        start_index = next((index for index, value in enumerate(dates) if comparator(value)), 0)
        end_index = min(start_index + window - 1, len(dates) - 1)
        return dates[start_index:end_index + 1]

    @staticmethod
    def _risk_rank(item: Dict[str, Any]) -> int:
        return {"severe-30d": 4, "severe-10d": 3, "delisting-warning": 2, "ordinary": 1}.get(str(item.get("riskTone")), 0)

    @staticmethod
    def _is_noise_stock(symbol: Any, name: Any) -> bool:
        """去除 5/1 开头基金类标的、ST/*ST 与退市整理期；正常 9 开头北交所股票保留。"""

        code_match = re.search(r"\d{6}", str(symbol or ""))
        code = code_match.group(0) if code_match else ""
        normalized = re.sub(r"\s+", "", str(name or "")).upper()
        return (
            code.startswith(("5", "1"))
            or normalized.startswith("ST")
            or normalized.startswith("*ST")
            or "退" in normalized
        )

    @staticmethod
    def _standard_ts_code(symbol: Any) -> str:
        """把公告源六位代码补成单股详情接口需要的标准代码。"""

        match = re.search(r"\d{6}", str(symbol or ""))
        if not match:
            return ""
        code = match.group(0)
        suffix = ".BJ" if code.startswith(("4", "8", "9")) else ".SH" if code.startswith("6") else ".SZ"
        return code + suffix

    @staticmethod
    def _event_end_date(announcement_date: str, body: str) -> str:
        """从严重异动正文提取不晚于公告日的最后事实交易日。"""

        if not body or not announcement_date:
            return ""
        text = re.sub(r"\s+", "", str(body)).replace("／", "/")

        def parse_fragment(fragment: str) -> str:
            match = re.search(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日?", fragment)
            if not match:
                match = re.search(r"(?:(20\d{2})[-/.])?(\d{1,2})[-/.](\d{1,2})", fragment)
            if not match:
                return ""
            try:
                year = int(match.group(1) or announcement_date[:4])
                value = datetime(year, int(match.group(2)), int(match.group(3))).strftime("%Y-%m-%d")
            except ValueError:
                return ""
            return value if value <= announcement_date else ""

        candidates: List[str] = []
        # 公告最稳定的事实口径：区间末日紧邻“连续 N 个交易日”。
        for match in re.finditer(
            r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日?)连续\d+个交易日",
            text,
        ):
            value = parse_fragment(match.group(1))
            if value:
                candidates.append(value)
        # 部分公告只写“截至某日收盘”，作为次优事实日期。
        for match in re.finditer(
            r"截至((?:20\d{2}年)?\d{1,2}月\d{1,2}日?)(?:收盘|，|,)",
            text,
        ):
            value = parse_fragment(match.group(1))
            if value:
                candidates.append(value)
        # Severe-abnormality announcements commonly say "自 YYYY年M月D日以来
        # 连续 N 个交易日" without spelling out the last date. The fact was
        # confirmed at the announcement close, so monitoring begins next session.
        if re.search(
            r"(?:自|从)?(?:20\d{2}年)?\d{1,2}月\d{1,2}日(?:以来|起).{0,24}?连续\d+个交易日",
            text,
        ):
            candidates.append(announcement_date)
        return max(candidates) if candidates else ""

    def _pdf_path(self, monitor_key: str) -> Path:
        """根据监控记录键生成固定 PDF 路径，不接受用户传入的文件名。"""

        safe_key = re.sub(r"[^a-zA-Z0-9_-]", "", str(monitor_key or ""))
        return self.pdf_dir / f"{safe_key}.pdf"

    def _cache_active_pdfs(self, items: List[Dict[str, Any]], today: str) -> None:
        """缓存当前严重异动期 PDF，并清理已出监管或已不在当前快照的文件。"""

        try:
            self.pdf_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        active_keys = set()
        for item in items:
            if item.get("riskTone") not in ("severe-10d", "severe-30d"):
                continue
            end_date = str(item.get("monitorEndDate") or "")
            if end_date and end_date < today:
                continue
            monitor_key = str(item.get("monitorKey") or "")
            source_candidates = [str(item.get("sourceUrl") or "")] + [str(url) for url in item.get("sourceUrls", [])]
            source_url = next((url for url in source_candidates if re.search(r"\.pdf(?:$|[?#])", url, re.I)), "")
            if not monitor_key or not source_url or not re.search(r"\.pdf(?:$|[?#])", source_url, re.I):
                continue
            path = self._pdf_path(monitor_key)
            active_keys.add(path.name)
            if path.exists():
                continue
            temporary = path.with_suffix(".part")
            try:
                request = Request(source_url, headers={"User-Agent": "Stock-Abnormal/1.0"})
                content = urlopen(request, timeout=15).read()
                if not content.startswith(b"%PDF"):
                    continue
                temporary.write_bytes(content)
                temporary.replace(path)
            except Exception:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            for path in self.pdf_dir.glob("*.pdf"):
                if path.name not in active_keys:
                    path.unlink(missing_ok=True)
            for path in self.pdf_dir.glob("*.part"):
                path.unlink(missing_ok=True)
        except OSError:
            pass

    def announcement_pdf_path(self, monitor_key: str) -> Optional[Path]:
        """仅允许读取当前监控期内的严重异动 PDF。"""

        snapshot = self.repository.active_snapshot()
        if not snapshot:
            return None
        today = self._clock().strftime("%Y-%m-%d")
        item = next((row for row in snapshot.get("items", []) if row.get("monitorKey") == monitor_key), None)
        if not item or item.get("riskTone") not in ("severe-10d", "severe-30d"):
            return None
        if self._is_expired(str(item.get("monitorEndDate") or ""), today):
            return None
        path = self._pdf_path(monitor_key)
        return path if path.is_file() else None

    def _collapse_by_stock(self, items: List[Dict[str, Any]], snapshot_date: str) -> List[Dict[str, Any]]:
        """同股同类型重叠时只保留最新记录；同日不同来源仅作为佐证链接附加。"""

        del snapshot_date  # 当前状态在 read 阶段按自然日判定。
        grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault((str(item.get("symbol")), str(item.get("riskTone"))), []).append(item)

        selected_items: List[Dict[str, Any]] = []
        for group in grouped.values():
            # 日期倒序、同日来源优先级升序：18.cn 优先于巨潮作为主链接。
            ordered = sorted(group, key=lambda item: (item.get("announcementDate", ""), -int(item.get("sourcePriority", 99))), reverse=True)
            for item in ordered:
                same_date = next((chosen for chosen in selected_items
                                  if chosen["symbol"] == item["symbol"]
                                  and chosen["riskTone"] == item["riskTone"]
                                  and chosen["announcementDate"] == item["announcementDate"]), None)
                if same_date:
                    urls = set(same_date.get("sourceUrls", []))
                    if item.get("sourceUrl"):
                        urls.add(item["sourceUrl"])
                    same_date["sourceUrls"] = sorted(urls)
                    same_date["sourceCount"] = len(urls)
                    if item.get("ruleResetConfirmed"):
                        same_date["ruleResetConfirmed"] = True
                        same_date["ruleResetDate"] = item.get("ruleResetDate") or same_date.get("ruleResetDate", "")
                        if item.get("resetEvidence"):
                            same_date["resetEvidence"] = item["resetEvidence"]
                    if item.get("eventDirection"):
                        # Prefer the direction parsed from the issuer/exchange
                        # notice when the primary feed only provides a summary.
                        same_date["eventDirection"] = item["eventDirection"]
                    same_date["monitorKey"] = hashlib.sha256("\n".join(sorted(
                        [same_date["sourceId"], item["sourceId"]]
                    )).encode("utf-8")).hexdigest()[:24]
                    continue
                overlaps = any(
                    chosen["symbol"] == item["symbol"]
                    and chosen["riskTone"] == item["riskTone"]
                    and item.get("monitorStartDate", "") <= chosen.get("monitorEndDate", "")
                    and item.get("monitorEndDate", "") >= chosen.get("monitorStartDate", "")
                    for chosen in selected_items
                )
                if overlaps:
                    # 最新记录已经覆盖这段时间，旧记录不再延长或合并。
                    continue
                selected_items.append({
                    **item,
                    "monitorKey": hashlib.sha256(str(item["sourceId"]).encode("utf-8")).hexdigest()[:24],
                    "sourceUrls": [item["sourceUrl"]] if item.get("sourceUrl") else [],
                    "sourceCount": 1 if item.get("sourceUrl") else 0,
                    "monitorPeriod": (
                        item.get("monitorPeriod")
                        if item.get("riskTone") == "delisting-warning"
                        else f"起始日起共 {MONITOR_PERIOD_TRADING_DAYS.get(item.get('riskTone'), 10)} 个交易日（起点计第1日）"
                    ),
                })

        # 只有来源正文明确复述严重异常波动事实的普通风险提示，才作为
        # 同一轮严重异动的佐证来源；不能仅凭同股同日把两个类别合并。
        # 独立风险提示仍保留独立卡片，退市整理始终独立。
        merged_items: List[Dict[str, Any]] = []
        for item in sorted(selected_items, key=self._risk_rank, reverse=True):
            if item.get("riskTone") != "ordinary" or item.get("sourceRelation") != "severe-supporting":
                merged_items.append(item)
                continue
            severe = next(
                (
                    candidate for candidate in merged_items
                    if candidate.get("symbol") == item.get("symbol")
                    and candidate.get("riskTone") in ("severe-10d", "severe-30d")
                    and candidate.get("announcementDate") == item.get("announcementDate")
                ),
                None,
            )
            if not severe:
                merged_items.append(item)
                continue
            urls = set(severe.get("supportingSourceUrls", []))
            if item.get("sourceUrl"):
                urls.add(str(item["sourceUrl"]))
            severe["supportingSourceUrls"] = sorted(urls)
            support = list(severe.get("supportingFacts") or [])
            if any(str(value.get("sourceId")) == str(item.get("sourceId")) for value in support):
                continue
            support.append({
                "sourceId": item.get("sourceId", ""),
                "title": item.get("title", ""),
                "announcementDate": item.get("announcementDate", ""),
                "riskTone": item.get("riskTone", ""),
                "monitorType": item.get("monitorType", ""),
                "source": item.get("source", ""),
                "sourceType": item.get("sourceType", ""),
                "sourceUrl": item.get("sourceUrl", ""),
                "confirmationStatus": item.get("confirmationStatus", ""),
                "contentHash": item.get("contentHash", ""),
                "sourceRelation": item.get("sourceRelation", ""),
            })
            severe["supportingFacts"] = support
        selected_items = merged_items
        for item in selected_items:
            if item.get("riskTone") in ("ordinary", "delisting-warning"):
                # 风险提示和退市整理风险警示只显示分类，不向小程序提供 PDF 入口。
                item["sourceUrl"] = ""
                item["sourceUrls"] = []
                item["sourceCount"] = 0
        return selected_items

    def refresh(self) -> Dict[str, Any]:
        """后台调用：公告源完整可用且快照校验通过后才原子发布。"""

        with self._lock:
            if self.refreshing:
                return {"started": False, "refreshing": True}
            self.refreshing = True
        now = self._clock()
        started_at = now.strftime("%Y-%m-%d %H:%M")
        # 允许失败后立即重试；秒级 ID 会在同一秒内重复并触发 SQLite 主键冲突。
        run_id = now.strftime("%Y%m%d%H%M%S%f")
        self.repository.set_state("last_attempt_date", now.strftime("%Y-%m-%d"))
        try:
            # 覆盖最长 30 个交易日判定回看窗口及其后 30 个自然日的公告校验窗口。
            start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
            broker_result = self.announcements.query_broker_risk_alerts(start_date, now.strftime("%Y-%m-%d"))
            broker_available = bool(broker_result.get("available"))
            broker_partial = bool(broker_result.get("partial"))
            zts_query = getattr(self.announcements, "query_zts_risk_alerts", None)
            zts_result = zts_query(start_date, now.strftime("%Y-%m-%d")) if callable(zts_query) else {"items": [], "available": False, "partial": False, "error": "未配置中泰公告源"}
            if not isinstance(zts_result, dict):
                zts_result = {"items": [], "available": False, "partial": False, "error": "中泰公告源返回格式错误"}
            zts_available = bool(zts_result.get("available"))
            zts_partial = bool(zts_result.get("partial"))
            # 两种来源都尝试：18.cn 成功时作为主源，18.cn 失败而巨潮成功时由巨潮接替主源。
            try:
                result = self.announcements.query_market(start_date, now.strftime("%Y-%m-%d"))
            except Exception as exc:  # noqa: BLE001 - 备源错误进入四态决策
                result = {"items": [], "available": False, "partial": True, "error": str(exc)}
            # A source may return usable official rows together with failures
            # for one exchange/keyword.  Do not discard those rows merely
            # because the aggregate source is marked partial; completeness is
            # reported separately in dataQuality.
            issuer_items = list(result.get("items") or [])
            issuer_supporting_items = list(result.get("supportingItems") or [])
            excluded_pending_source_ids = set()
            for supporting_item in issuer_supporting_items:
                pending_source_key = supporting_item.get("url") or (
                    f"{supporting_item.get('stockCode')}:{supporting_item.get('date')}:{supporting_item.get('title')}"
                )
                normalized_reply_title = re.sub(r"\s+", "", str(supporting_item.get("title") or ""))
                ordinary_reply = "股票交易异常波动" in normalized_reply_title and "严重异常波动" not in normalized_reply_title
                excluded_pending_source_ids.add(hashlib.sha256(pending_source_key.encode("utf-8")).hexdigest()[:24])
                if ordinary_reply:
                    excluded_pending_source_ids.add(hashlib.sha256(f"ordinary-reset:{pending_source_key}".encode("utf-8")).hexdigest()[:24])
            issuer_available = bool(result.get("available")) and not bool(result.get("partial"))
            issuer_has_rows = bool(issuer_items)
            if not broker_available and not issuer_has_rows and not zts_available:
                raise RuntimeError("18.cn、巨潮资讯与中泰证券公告源均不可用")
            parsed_events: List[Dict[str, Any]] = []
            parsed_reset_facts: List[Dict[str, Any]] = []
            raw_announcements: List[Dict[str, Any]] = []
            issuer_body_checked = 0
            issuer_body_available = 0
            issuer_body_ocr = 0
            issuer_body_ocr_failed = 0
            issuer_reset_verified = 0
            issuer_unread_facts: List[Dict[str, Any]] = []
            for announcement in issuer_items if (issuer_available or issuer_has_rows) else []:
                title = announcement["title"]
                if isinstance(self.announcements, AnnouncementService):
                    body_result = self.announcements.extract_pdf_body(announcement["url"])
                else:  # compatibility for injected test and legacy readers
                    body_result = {"text": self.announcements.extract_pdf_text(announcement["url"]), "method": "text_layer"}
                body = str(body_result.get("text") or "")
                issuer_body_checked += 1
                issuer_body_ocr += int(body_result.get("method") == "ocr")
                issuer_body_ocr_failed += int(str(body_result.get("error") or "").startswith("ocr_"))
                if str(body or "").strip():
                    issuer_body_available += 1
                else:
                    # A failed正文 read is an unresolved official hit, not an
                    # empty fact set.  Keep it auditable for the next retry.
                    pending_source_key = announcement.get("url") or f"{announcement.get('stockCode')}:{announcement.get('date')}:{title}"
                    pending_is_ordinary = (
                        "股票交易异常波动" in re.sub(r"\s+", "", title)
                        and "严重异常波动" not in re.sub(r"\s+", "", title)
                        and not re.search(r"风险提示|风险警示|退市整理|停牌核查", title)
                    )
                    issuer_unread_facts.append({
                        "sourceId": hashlib.sha256(
                            (f"ordinary-reset:{pending_source_key}" if pending_is_ordinary else pending_source_key).encode("utf-8")
                        ).hexdigest()[:24],
                        "symbol": announcement.get("stockCode", ""),
                        "name": announcement.get("stockName", ""),
                        "title": title,
                        "announcementDate": announcement.get("date", ""),
                        "eventDate": announcement.get("date", ""),
                        "sourceUrl": announcement.get("url", ""),
                        "factType": "ordinary-abnormality" if pending_is_ordinary else "announcement-hit",
                        "countsForSameDirection": True if pending_is_ordinary else False,
                        "status": "pending_verification",
                        "reason": str(body_result.get("error") or "official_body_unreadable"),
                        "bodyReadMethod": str(body_result.get("method") or "unavailable"),
                        "bodyReadConfidence": body_result.get("confidence"),
                        "bodyReadPages": int(body_result.get("pages") or 0),
                        "bodyReadError": str(body_result.get("error") or ""),
                    })
                if announcement.get("date"):
                    ordinary_evidence = self._ordinary_reset_evidence(
                        title, body, announcement.get("stockCode", ""),
                        "issuer-disclosure", announcement.get("url", ""),
                    )
                    if ordinary_evidence:
                        event_end_date = self._event_end_date(announcement["date"], body)
                        if event_end_date:
                            reset_dates = self._monitor_period_dates(
                                event_end_date, "issuer-disclosure", "ordinary",
                                announcement.get("stockCode", ""), event_end_date=event_end_date,
                            )
                        else:
                            reset_dates = self._monitor_period_dates(
                                announcement["date"], "broker-risk-alert", "ordinary",
                                announcement.get("stockCode", ""),
                            )
                        if reset_dates:
                            source_key = announcement.get("url") or f"{announcement.get('stockCode')}:{announcement['date']}:{title}"
                            source_id = hashlib.sha256(f"ordinary-reset:{source_key}".encode("utf-8")).hexdigest()[:24]
                            parsed_reset_facts.append({
                                "sourceId": source_id, "symbol": announcement["stockCode"],
                                "name": announcement["stockName"], "title": title,
                                "source": announcement.get("source", "巨潮资讯"),
                                "sourceUrl": announcement.get("url", ""),
                                "contentHash": hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest(),
                                "confirmationStatus": (
                                    "official-ordinary-abnormality-body-confirmed"
                                    if ordinary_evidence.get("code") == ORDINARY_RESET_EVIDENCE_CODE
                                    else "official-ordinary-abnormality-title-market-pending-body"
                                ),
                                "monitorType": "行情重置事实（非重点监控）",
                                "sourceRelation": "ordinary-abnormality-reset",
                                "announcementDate": announcement["date"],
                                "eventDate": event_end_date or announcement["date"],
                                "eventDirection": self._ordinary_event_direction(body),
                                "bodyReadMethod": str(body_result.get("method") or "text_layer"),
                                "bodyReadConfidence": body_result.get("confidence"),
                                "countsForSameDirection": bool(ordinary_evidence.get("countsForSameDirection")),
                                "ruleResetDate": reset_dates[0].replace("-", ""),
                                # 官方普通异动公告本身确认 3 日基线重置；
                                # 同向次数仍由行情模块单独核验，不由标题直接 +1。
                                "ruleResetConfirmed": True,
                                "riskTone": "ordinary-abnormality",
                                "sourceType": "issuer-disclosure",
                                "resetEvidence": ordinary_evidence,
                            })
                            if ordinary_evidence.get("code") == ORDINARY_RESET_EVIDENCE_CODE:
                                issuer_reset_verified += 1
                monitor_type, risk_tone = self._issuer_monitor_type(title, body, announcement.get("stockCode", ""))
                # 巨潮资讯是备源：必须按同一正文语义规则确认风险类型，普通公司风险公告丢弃。
                if not risk_tone:
                    continue
                if not announcement.get("date"):
                    continue
                event_end_date = self._event_end_date(announcement["date"], body) if risk_tone.startswith("severe-") else ""
                monitor_dates = self._monitor_period_dates(
                    event_end_date or announcement["date"], "issuer-disclosure", risk_tone,
                    announcement.get("stockCode", ""), event_end_date=event_end_date,
                )
                if not monitor_dates:
                    continue
                monitor_start, monitor_end = monitor_dates[0], monitor_dates[-1]
                source_key = announcement["url"] or f"{announcement['stockCode']}:{announcement['date']}:{title}"
                source_id = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:24]
                parsed_events.append({
                    "sourceId": source_id, "monitorKey": source_id,
                    "symbol": announcement["stockCode"], "name": announcement["stockName"],
                    "title": title, "announcementDate": announcement["date"],
                    "monitorStart": monitor_start[5:], "monitorStartDate": monitor_start,
                    "monitorEnd": monitor_end[5:] if monitor_end else "待核实",
                    "monitorEndDate": monitor_end, "days": None, "daysTotal": MONITOR_PERIOD_TRADING_DAYS.get(risk_tone, 10),
                    "monitorDates": monitor_dates,
                    # A severe classification is a monitoring fact, not proof
                    # that the source is an official reset notice.  The reset
                    # flag is set only after explicit body evidence is present.
                    "ruleResetDate": monitor_dates[0].replace("-", "") if event_end_date and monitor_dates else "",
                    "ruleResetConfirmed": False,
                    "resetEvidence": None,
                    "eventDirection": self._severe_event_direction(body) if risk_tone.startswith("severe-") else "",
                    "bodyReadMethod": str(body_result.get("method") or "text_layer"),
                    "bodyReadConfidence": body_result.get("confidence"),
                    "monitorType": monitor_type, "riskTone": risk_tone,
                    "sourceRelation": self._risk_prompt_relation(title, body, risk_tone),
                    "isST": "ST" in announcement["stockName"].upper(),
                    "source": announcement["source"], "sourceUrl": announcement["url"] if risk_tone != "ordinary" else "",
                    "sourceType": "issuer-disclosure", "sourceRole": "issuer_disclosure_backup",
                    "sourcePriority": 2, "sourceLabel": "巨潮资讯备源",
                    "confirmationStatus": "issuer-disclosure-confirmed",
                    "contentHash": hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest(),
                    "monitorPeriod": f"起始日起共 {MONITOR_PERIOD_TRADING_DAYS.get(risk_tone, 10)} 个交易日（起点计第1日）",
                })
                reset_evidence = self._reset_evidence(
                    title, body, announcement.get("stockCode", ""), risk_tone,
                    "issuer-disclosure", announcement.get("url", ""),
                )
                if reset_evidence:
                    parsed_events[-1]["ruleResetConfirmed"] = True
                    parsed_events[-1]["resetEvidence"] = reset_evidence
                    issuer_reset_verified += 1
            for alert in broker_result.get("items", []) if broker_available else []:
                # 18.cn 是主源；只有正文满足风险提示或 10/30 日阈值语义才进入重点监控。
                if not self._is_accepted_broker_alert(alert["title"], alert.get("body", ""), alert.get("stockCode", "")):
                    continue
                monitor_type, risk_tone = self._broker_monitor_type(alert["title"], alert.get("body", ""), alert.get("stockCode", ""))
                event_end_date = self._event_end_date(alert["date"], alert.get("body", "")) if risk_tone.startswith("severe-") else ""
                monitor_dates = self._monitor_period_dates(
                    event_end_date or alert["date"], alert["sourceType"], risk_tone, alert.get("stockCode", ""),
                    event_end_date=event_end_date,
                )
                if not monitor_dates:
                    continue
                monitor_start, monitor_end = monitor_dates[0], monitor_dates[-1]
                # 页面脚本中的无关数字不单独触发，分类已在正文语义规则中完成。
                source_id = hashlib.sha256(alert["url"].encode("utf-8")).hexdigest()[:24]
                parsed_events.append({
                    "sourceId": source_id, "monitorKey": source_id,
                    "symbol": alert["stockCode"], "name": alert["stockName"],
                    "title": alert["title"], "announcementDate": alert["date"],
                    "monitorStart": monitor_start[5:], "monitorStartDate": monitor_start,
                    "monitorEnd": monitor_end[5:], "monitorEndDate": monitor_end,
                    "days": None, "daysTotal": MONITOR_PERIOD_TRADING_DAYS.get(risk_tone, 10), "monitorDates": monitor_dates,
                    "monitorType": monitor_type, "riskTone": risk_tone,
                    "sourceRelation": self._risk_prompt_relation(alert["title"], alert.get("body", ""), risk_tone),
                    "eventDirection": self._severe_event_direction(alert.get("body", "")) if risk_tone.startswith("severe-") else "",
                    "isST": "ST" in alert["stockName"].upper(),
                    "source": alert["source"], "sourceUrl": alert["url"] if risk_tone != "ordinary" else "",
                    "sourceType": alert["sourceType"], "sourceRole": alert["sourceRole"],
                    "sourcePriority": alert["sourcePriority"], "sourceLabel": alert["sourceLabel"],
                    "confirmationStatus": "broker-notice-quotes-exchange-status",
                    "ruleResetConfirmed": False,
                    "ruleResetDate": "",
                    "resetEvidence": None,
                    "contentHash": hashlib.sha256(alert["body"].encode("utf-8")).hexdigest(),
                    "monitorPeriod": f"起始日起共 {MONITOR_PERIOD_TRADING_DAYS.get(risk_tone, 10)} 个交易日（起点计第1日）",
                })
            for alert in zts_result.get("items", []) if zts_available else []:
                monitor_type, risk_tone = self._zts_monitor_type(
                    alert["title"], alert.get("body", ""), alert.get("riskToneHint", "")
                )
                if not risk_tone or not alert.get("date"):
                    continue
                source_id = hashlib.sha256(alert["url"].encode("utf-8")).hexdigest()[:24]
                raw_announcements.append({
                    "sourceId": source_id, "symbol": alert["stockCode"], "name": alert["stockName"],
                    "announcementDate": alert["date"], "title": alert["title"], "source": alert["source"],
                    "sourceUrl": alert["url"],
                    "contentHash": hashlib.sha256(f"{alert['title']}\n{alert.get('body', '')}".encode("utf-8")).hexdigest(),
                    "confirmationStatus": "broker-disclosure-confirmed",
                    "monitorType": monitor_type, "riskTone": risk_tone, "sourceType": "zts-risk-alert",
                })
                if risk_tone == "delisting-warning":
                    monitor_dates, monitor_start, monitor_end = self._delisting_period_dates(
                        alert.get("body", ""), alert["date"], alert.get("stockCode", "")
                    )
                else:
                    monitor_dates = self._monitor_period_dates(
                        alert["date"], "zts-risk-alert", risk_tone, alert.get("stockCode", "")
                    )
                    monitor_start = monitor_dates[0] if monitor_dates else ""
                    monitor_end = monitor_dates[-1] if monitor_dates else ""
                if not monitor_dates:
                    continue
                parsed_events.append({
                    "sourceId": source_id, "monitorKey": source_id,
                    "symbol": alert["stockCode"], "name": alert["stockName"],
                    "title": alert["title"], "announcementDate": alert["date"],
                    "monitorStart": monitor_start[5:], "monitorStartDate": monitor_start,
                    "monitorEnd": monitor_end[5:], "monitorEndDate": monitor_end,
                    "days": None, "daysTotal": len(monitor_dates),
                    "monitorDates": monitor_dates, "monitorType": monitor_type, "riskTone": risk_tone,
                    "sourceRelation": self._risk_prompt_relation(alert["title"], alert.get("body", ""), risk_tone),
                    "isST": "ST" in alert.get("stockName", "").upper(),
                    "source": alert["source"], "sourceUrl": alert["url"],
                    "sourceType": "zts-risk-alert", "sourceRole": "zts_primary",
                    "sourcePriority": alert["sourcePriority"], "sourceLabel": alert["sourceLabel"],
                    "confirmationStatus": "broker-disclosure-confirmed",
                    "ruleResetConfirmed": False,
                    "ruleResetDate": "",
                    "resetEvidence": None,
                    "contentHash": hashlib.sha256(f"{alert['title']}\n{alert.get('body', '')}".encode("utf-8")).hexdigest(),
                    "monitorPeriod": (
                        f"退市整理期至 {monitor_end}"
                        if risk_tone == "delisting-warning"
                        else f"起始日起共 {MONITOR_PERIOD_TRADING_DAYS.get(risk_tone, 10)} 个交易日（起点计第1日）"
                    ),
                })
            # 18.cn 页面只展示滚动的最近条目；仅在 18.cn 成功时保留旧窗口，避免主源滚动造成记录消失。
            previous_snapshot = self.repository.active_snapshot() or {}
            # 正文读取失败的官方命中不能因下一次关键词分页变化而消失。
            # 保留 90 日内未被同源正式正文事实替换的 pending 记录；正文
            # 恢复后使用相同 sourceId 写入正式事实时自动移除。
            confirmed_fact_ids = {
                str(item.get("sourceId") or "")
                for item in [*parsed_reset_facts, *parsed_events]
                if item.get("ruleResetConfirmed")
                and (item.get("resetEvidence") or {}).get("code") != ORDINARY_TITLE_EVIDENCE_CODE
            }
            pending_by_id = {
                str(item.get("sourceId") or ""): item
                for item in [*(previous_snapshot.get("unresolvedFacts") or []), *issuer_unread_facts]
                if item.get("sourceId")
                and str(item.get("announcementDate") or "") >= start_date
                and str(item.get("sourceId")) not in confirmed_fact_ids
                and str(item.get("sourceId")) not in excluded_pending_source_ids
            }
            issuer_unread_facts = sorted(
                pending_by_id.values(),
                key=lambda item: (str(item.get("announcementDate") or ""), str(item.get("sourceId") or "")),
            )
            # 保留近期按类型确认的重置事实，才能重建普通基线分段及严重计次账本；
            # 10 日滚动统计在长假时也需要覆盖超过一个月的事实边界。
            reset_by_id = {
                str(item.get("sourceId") or ""): item
                for item in [*(previous_snapshot.get("resetFacts") or []), *parsed_reset_facts]
                if item.get("sourceId")
            }
            reset_cutoff = (now - timedelta(days=60)).strftime("%Y%m%d")
            reset_facts = [
                item for item in reset_by_id.values()
                if str(item.get("ruleResetDate") or "").replace("-", "") >= reset_cutoff
            ]
            reset_facts = sorted(
                reset_facts,
                key=lambda item: (
                    str(item.get("symbol") or ""),
                    str(item.get("ruleResetDate") or ""),
                    str(item.get("announcementDate") or ""),
                ),
            )
            previous_items = [
                item for item in previous_snapshot.get("items", [])
                if str(item.get("monitorEndDate") or "") >= (now - timedelta(days=30)).strftime("%Y-%m-%d")
            ]
            # 源站滚动、单条详情失败或短时不可用时，保留尚未到期的旧记录；
            # 新公告按同股同类型的最新记录覆盖旧记录。这样不会因为一次
            # 部分抓取把闽东、天普等仍在监管期内的卡片清空。
            merge_items = parsed_events + previous_items
            parsed = self._collapse_by_stock(merge_items, now.strftime("%Y-%m-%d"))
            self._cache_active_pdfs(parsed, now.strftime("%Y-%m-%d"))
            updated_at = self._clock().strftime("%Y-%m-%d %H:%M")
            active_source = "broker_primary" if broker_available else ("zts_primary" if zts_available else "issuer_disclosure_backup")
            source_label = "18.cn 主源（部分详情失败）" if broker_available and broker_partial else (
                "18.cn 主源" if broker_available else (
                    "中泰证券公告" if zts_available else
                    "巨潮资讯备源（部分结果）" if not issuer_available else "巨潮资讯备源（主源降级接替）"
                )
            )
            snapshot = {
                "snapshotId": run_id, "snapshotDate": now.strftime("%Y-%m-%d"),
                "asOfTradeDate": "",
                "startedAt": started_at, "updatedAt": updated_at, "items": parsed,
                "refreshing": False, "error": "",
                "dataQuality": {
                    "source": source_label,
                    "announcementStatus": "source-specific",
                    "verifiedSources": ["东方财富证券 18.cn", "巨潮资讯", "中泰证券"],
                    "sourcePolicy": [
                        {"role": "broker_primary", "label": "18.cn 主源", "priority": 1, "status": "degraded" if broker_partial or not broker_available else "available"},
                        {"role": "issuer_disclosure_backup", "label": "巨潮资讯备源", "priority": 2, "status": "available" if issuer_available else ("partial" if issuer_has_rows else "unavailable")},
                        {"role": "zts_primary", "label": "中泰证券公告", "priority": 1, "status": "degraded" if zts_partial or not zts_available else "available"},
                    ],
                    "sourceHealth": {
                        "brokerPrimary": "degraded" if broker_partial or not broker_available else "available",
                        "issuerBackup": "available" if issuer_available else ("degraded" if result.get("partial") or issuer_has_rows else "unavailable"),
                        "brokerError": str(broker_result.get("error") or "") if broker_partial else ("" if broker_available else "18.cn 未完整返回"),
                        "issuerError": "" if issuer_available else str(result.get("error") or "巨潮资讯未完整返回"),
                        "issuerStatus": str(result.get("status") or ("available" if issuer_available else ("partial" if issuer_has_rows else "unavailable"))),
                        "ztsSource": "degraded" if zts_partial or not zts_available else "available",
                        "zts": "degraded" if zts_partial or not zts_available else "available",
                        "ztsError": str(zts_result.get("error") or "") if zts_partial or not zts_available else "",
                    },
                    "resetPolicy": {
                        "authorizedSourceTypes": sorted(RESET_AUTHORIZED_SOURCE_TYPES),
                        "evidenceCodes": [RESET_EVIDENCE_CODE, ORDINARY_RESET_EVIDENCE_CODE, ORDINARY_TITLE_EVIDENCE_CODE],
                        "requiresOfficialBodyForSevere": True,
                        "ordinaryTitleMarketFallback": True,
                        "ordinaryRequiresThreeDayThresholdOrTurnoverCondition": True,
                        "severeRequiresBoardThresholdOrSameDirectionEvidence": True,
                        "ordinaryResetScope": ["3d_baseline"],
                        "severeResetScope": ["3d_baseline", "10d", "30d", "same_direction_10d"],
                        "ordinaryThreeDayNoticeAddsOneDirectionalEvent": True,
                        "overlappingThreeDayWindowsCountSeparately": False,
                        "sameDirectionEventPolicy": "product-inference: formal-ledger-counts-idempotent-cninfo-confirmed-ordinary-3d-notices; market-windows-only-supply-direction-candidates; distinct-confirmed-notices-count-separately",
                        "ordinaryRiskAlertsReset": False,
                    },
                    "issuerBodyVerification": {
                        "checked": issuer_body_checked,
                        "bodyAvailable": issuer_body_available,
                        "bodyUnavailable": max(0, issuer_body_checked - issuer_body_available),
                        "ocrUsed": issuer_body_ocr,
                        "ocrFailed": issuer_body_ocr_failed,
                        "resetEvidenceVerified": issuer_reset_verified,
                        "resetFactsCount": len(reset_facts),
                        "unreadFacts": issuer_unread_facts,
                        "status": "pending_verification" if issuer_unread_facts else "verified",
                    },
                    "activeSource": active_source,
                    "isOfficialMonitorPeriod": False,
                    "periodCalculation": "监管期按交易日计算并将起点计为第1日；3日普通异动不进入重点监控，10日和30日严重异动触发后均监管10个交易日；30日仅是判定回看窗口",
                    "coverage": "18.cn 与中泰公告独立采集，巨潮资讯为发行人备源；同源记录按股票和风险类型取最新；退市整理风险警示独立保留",
                },
                "resetFacts": reset_facts,
                # Official search hits whose正文 could not be fetched are
                # retained separately so consumers cannot mistake them for
                # an empty announcement result.
                "unresolvedFacts": issuer_unread_facts,
            }
            raw_announcements.extend(parsed_events)
            raw_announcements.extend(parsed_reset_facts)
            self.repository.publish(snapshot, raw_announcements, run_id)
            self.repository.set_state("monitor_rule_version", MONITOR_RULE_VERSION)
            self.last_error = ""
            callback = self._snapshot_published_callback
            if callback:
                Thread(
                    target=callback,
                    name="prediction-facts-rebuild",
                    daemon=True,
                ).start()
            return {"started": True, "refreshing": False, "count": len(parsed)}
        except Exception as exc:
            self.last_error = str(exc)
            self.repository.record_failure(run_id, started_at, self.last_error)
            return {"started": True, "refreshing": False, "error": self.last_error}
        finally:
            self.refreshing = False

    def read(self, status: str, monitor_type: str) -> Dict[str, Any]:
        if status != "current":
            raise ValueError("重点监控仅支持 current 当前监控")
        snapshot = self.repository.active_snapshot()
        if not snapshot:
            return {
                # 没有成功快照时只在后台线程确实仍在采集才返回 refreshing。
                # 采集失败后必须进入错误空态，避免小程序无限显示骨架或轮询。
                "tab": status, "items": [], "updatedAt": "", "refreshing": bool(self.refreshing),
                "error": self.last_error or ("今日监控快照尚未生成，后台正在获取" if self.refreshing else "今日监控快照获取失败，请稍后重试"),
                "dataQuality": {"source": "公告快照", "isComplete": False},
            }
        # 读取接口只使用快照字段和上海本地日期，不会触发上游请求。
        today = self._clock().strftime("%Y-%m-%d")
        items = []
        for item in snapshot.get("items", []):
            end_date = str(item.get("monitorEndDate") or "")
            if self._is_expired(end_date, today):
                continue
            if monitor_type == "risk" and item.get("riskTone") != "ordinary":
                continue
            if monitor_type == "delisting" and item.get("riskTone") != "delisting-warning":
                continue
            if monitor_type == "severe" and item.get("riskTone") not in ("severe-10d", "severe-30d"):
                continue
            if monitor_type in ("ordinary", "severe_10d", "severe_30d"):
                expected = {"ordinary": "ordinary", "severe_10d": "severe-10d", "severe_30d": "severe-30d"}[monitor_type]
                if item.get("riskTone") != expected:
                    continue
            # 页面展示“还剩几天”按自然日计算；monitorDates 仍保留用于审计监管交易日序列。
            remaining = self._remaining_natural_days(end_date, today)
            row = {**item, "ts_code": item.get("ts_code") or self._standard_ts_code(item.get("symbol", "")), "days": remaining}
            if item.get("riskTone") in ("severe-10d", "severe-30d"):
                row["pdfCached"] = bool(self.announcement_pdf_path(str(item.get("monitorKey") or "")))
            items.append(row)
        # 监管期按快照保存的交易日序列倒计时；未公开结束日的记录排在已确认日期之后。
        items.sort(key=lambda item: (item.get("days") is None, item.get("days") if item.get("days") is not None else 9999, item.get("announcementDate", "")))
        return {
            **snapshot,
            "tab": status,
            "items": items,
            "refreshing": self.refreshing,
            "error": self.last_error or snapshot.get("error", ""),
        }

    @staticmethod
    def _is_expired(end_date: str, today: str) -> bool:
        """结束日期当天仍为当前监控，次日才从当前列表移除。"""

        return bool(end_date and end_date < today)

    @staticmethod
    def _remaining_natural_days(end_date: str, today: str) -> Optional[int]:
        """按自然日倒计时；来源未给结束日期时不生成猜测值。"""

        if not end_date:
            return None
        return max(0, (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days)

    @staticmethod
    def _remaining_trading_days(monitor_dates: Any, today: str) -> Optional[int]:
        """结束日当天仍返回 1，次日由当前列表过滤；只按已冻结交易日计算。"""

        if not isinstance(monitor_dates, list) or not monitor_dates:
            return None
        return sum(1 for value in monitor_dates if str(value) >= today)
