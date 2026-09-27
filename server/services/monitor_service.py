"""公开公告驱动的监管池服务。

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
from urllib.request import Request, urlopen

from .announcement_service import AnnouncementService
from .tushare_client import TushareClient

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone(timedelta(hours=8))
MONITOR_WINDOW_OFFSET_DAYS = 21  # 起始日加 21 个自然日；产品监控口径，不代表法定期限。


class MonitorRepository:
    """用事务保存公告元数据和当前监控快照，不保存公告文件或正文。"""

    def __init__(self, path: Optional[str] = None) -> None:
        value = path or os.getenv("MONITOR_DB_PATH", "/tmp/stock-abnormal/monitor.db")
        self.path = Path(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
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
                    fetched_at TEXT NOT NULL
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
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in announcements:
                db.execute("""
                    INSERT OR REPLACE INTO announcement_raw
                    (source_id, stock_code, stock_name, announcement_date, title, source, source_url,
                     content_hash, confirmation_status, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item["sourceId"], item["symbol"], item["name"], item["announcementDate"],
                    item["title"], item["source"], item["sourceUrl"], item["contentHash"],
                    item["confirmationStatus"], snapshot["updatedAt"],
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

    def record_failure(self, run_id: str, started_at: str, message: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR REPLACE INTO ingest_run VALUES (?, ?, ?, 'failed', 0, ?)", (
                run_id, started_at, datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M"), message,
            ))
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES ('last_status', 'failed')")
            db.execute("INSERT OR REPLACE INTO monitor_state VALUES ('last_failure_date', ?)", (started_at[:10],))
            db.commit()


class OfficialMonitorService:
    """采集公开披露公告并生成监管池快照，不读取全市场交易行情。"""

    def __init__(self, client: TushareClient, announcements: Optional[AnnouncementService] = None) -> None:
        self.client = client
        self.announcements = announcements or AnnouncementService()
        self.repository = MonitorRepository()
        # 只缓存当前严重异动期内且可直接下载的 PDF；默认使用容器临时盘，
        # 可通过 MONITOR_PDF_DIR 指向持久化卷。文件不进入 SQLite，也不写入小程序。
        self.pdf_dir = Path(os.getenv("MONITOR_PDF_DIR", "/tmp/stock-abnormal/monitor-pdf"))
        self.refreshing = False
        self.last_error = ""
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

    def _scheduler_loop(self) -> None:
        # 启动后先读当前快照；00:00 后每日首次采集，失败只在次日 01:00-09:00 每小时重试。
        self._scheduler_stop.wait(0.5)
        while not self._scheduler_stop.is_set():
            try:
                now = datetime.now(SHANGHAI_TZ)
                today = now.strftime("%Y-%m-%d")
                state = self.repository.states()
                last_attempt_date = state.get("last_attempt_date", "")
                # 无当前快照时冷启动立即补采；有当前快照时每天 00:00 后只尝试一次。
                if (not self.repository.active_snapshot() and not last_attempt_date) or (
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
    def _classify_monitor_text(title: str, body: str) -> tuple[str, str]:
        """按 18.cn/巨潮正文语义分类：30 日、10 日、风险提示；无法确认则丢弃。"""

        # 标题只用于日志定位，分类严格基于正文；正文为空时不纳入。
        del title
        text = re.sub(r"\s+", "", body or "").replace("％", "%")
        has_30_window = "30个交易日" in text
        has_10_window = "10个交易日" in text
        has_30_threshold = bool(
            re.search(r"30个交易日.{0,160}(?:200|300)(?:\.\d+)?%", text)
            or re.search(r"(?:200|300)(?:\.\d+)?%.{0,160}30个交易日", text)
        )
        has_10_threshold = bool(
            re.search(r"10个交易日.{0,160}(?:100|150)(?:\.\d+)?%", text)
            or re.search(r"(?:100|150)(?:\.\d+)?%.{0,160}10个交易日", text)
        )
        has_risk_wording = (
            "交易所将对以上证券的异常交易行为进行从严认定" in text
        )
        if has_30_window and has_30_threshold:
            return "30日严重异动", "severe-30d"
        if has_10_window and has_10_threshold:
            return "10日严重异动", "severe-10d"
        if has_risk_wording:
            return "风险提示", "ordinary"
        return "", ""

    @staticmethod
    def _monitor_type(title: str, body: str) -> tuple[str, str]:
        return OfficialMonitorService._classify_monitor_text(title, body)

    @staticmethod
    def _broker_monitor_type(title: str, body: str) -> tuple[str, str]:
        return OfficialMonitorService._classify_monitor_text(title, body)

    @staticmethod
    def _is_accepted_broker_alert(title: str, body: str) -> bool:
        """18.cn 只接受正文能归类为风险提示或严重异动的记录。"""

        return bool(OfficialMonitorService._classify_monitor_text(title, body)[1])

    @staticmethod
    def _monitor_period(source_date: str, source_type: str) -> tuple[str, str]:
        """将来源日期转换为截图口径；券商提示次日生效，公告以公告日生效。"""

        start = datetime.strptime(source_date, "%Y-%m-%d")
        if source_type == "broker-risk-alert":
            start += timedelta(days=1)
        end = start + timedelta(days=MONITOR_WINDOW_OFFSET_DAYS)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    @staticmethod
    def _risk_rank(item: Dict[str, Any]) -> int:
        return {"severe-30d": 3, "severe-10d": 2, "ordinary": 1}.get(str(item.get("riskTone")), 0)

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
        today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
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
                    "monitorPeriod": f"以公告记录起始日后 {MONITOR_WINDOW_OFFSET_DAYS} 个自然日为结束日；非交易所法定期限",
                })
        for item in selected_items:
            if item.get("riskTone") == "ordinary":
                # 风险提示只显示分类，不向小程序提供公告入口。
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
        now = datetime.now(SHANGHAI_TZ)
        started_at = now.strftime("%Y-%m-%d %H:%M")
        # 允许失败后立即重试；秒级 ID 会在同一秒内重复并触发 SQLite 主键冲突。
        run_id = now.strftime("%Y%m%d%H%M%S%f")
        self.repository.set_state("last_attempt_date", now.strftime("%Y-%m-%d"))
        try:
            # 覆盖最长 30 个交易日监管窗口及其后 30 个自然日的当前记录校验窗口。
            start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
            broker_result = self.announcements.query_broker_risk_alerts(start_date, now.strftime("%Y-%m-%d"))
            broker_available = bool(broker_result.get("available"))
            # 两种来源都尝试：18.cn 成功时作为主源，18.cn 失败而巨潮成功时由巨潮接替主源。
            try:
                result = self.announcements.query_market(start_date, now.strftime("%Y-%m-%d"))
            except Exception as exc:  # noqa: BLE001 - 备源错误进入四态决策
                result = {"items": [], "available": False, "partial": True, "error": str(exc)}
            issuer_available = bool(result.get("available")) and not bool(result.get("partial"))
            if not broker_available and not issuer_available:
                raise RuntimeError("18.cn 主源与巨潮资讯备源均不可用")
            parsed_events: List[Dict[str, Any]] = []
            for announcement in result.get("items", []) if issuer_available else []:
                title = announcement["title"]
                # 一般性的公司风险提示不等于交易所重点监控提示；后者单独使用 18.cn 券商公告源。
                if "异常波动" not in title:
                    continue
                body = self.announcements.extract_pdf_text(announcement["url"])
                monitor_type, risk_tone = self._monitor_type(title, body)
                # 巨潮资讯是备源：必须按同一正文语义规则确认风险类型，普通公司风险公告丢弃。
                if not risk_tone:
                    continue
                if not announcement.get("date"):
                    continue
                monitor_start, monitor_end = self._monitor_period(announcement["date"], "issuer-disclosure")
                source_key = announcement["url"] or f"{announcement['stockCode']}:{announcement['date']}:{title}"
                source_id = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:24]
                parsed_events.append({
                    "sourceId": source_id, "monitorKey": source_id,
                    "symbol": announcement["stockCode"], "name": announcement["stockName"],
                    "title": title, "announcementDate": announcement["date"],
                    "monitorStart": monitor_start[5:], "monitorStartDate": monitor_start,
                    "monitorEnd": monitor_end[5:] if monitor_end else "待核实",
                    "monitorEndDate": monitor_end, "days": None, "daysTotal": MONITOR_WINDOW_OFFSET_DAYS + 1,
                    "monitorDates": [],
                    "monitorType": monitor_type, "riskTone": risk_tone,
                    "isST": "ST" in announcement["stockName"].upper(),
                    "source": announcement["source"], "sourceUrl": announcement["url"] if risk_tone != "ordinary" else "",
                    "sourceType": "issuer-disclosure", "sourceRole": "issuer_disclosure_backup",
                    "sourcePriority": 2, "sourceLabel": "巨潮资讯备源",
                    "confirmationStatus": "issuer-disclosure-confirmed",
                    "contentHash": hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest(),
                    "monitorPeriod": f"公告日起至第 {MONITOR_WINDOW_OFFSET_DAYS} 个自然日",
                })
            for alert in broker_result.get("items", []) if broker_available else []:
                # 18.cn 是主源；只有正文满足风险提示或 10/30 日阈值语义才进入监控池。
                if not self._is_accepted_broker_alert(alert["title"], alert.get("body", "")):
                    continue
                monitor_start, monitor_end = self._monitor_period(alert["date"], alert["sourceType"])
                # 页面脚本中的无关数字不单独触发，分类已在正文语义规则中完成。
                monitor_type, risk_tone = self._broker_monitor_type(alert["title"], alert.get("body", ""))
                source_id = hashlib.sha256(alert["url"].encode("utf-8")).hexdigest()[:24]
                parsed_events.append({
                    "sourceId": source_id, "monitorKey": source_id,
                    "symbol": alert["stockCode"], "name": alert["stockName"],
                    "title": alert["title"], "announcementDate": alert["date"],
                    "monitorStart": monitor_start[5:], "monitorStartDate": monitor_start,
                    "monitorEnd": monitor_end[5:], "monitorEndDate": monitor_end,
                    "days": None, "daysTotal": MONITOR_WINDOW_OFFSET_DAYS + 1, "monitorDates": [],
                    "monitorType": monitor_type, "riskTone": risk_tone,
                    "isST": "ST" in alert["stockName"].upper(),
                    "source": alert["source"], "sourceUrl": alert["url"] if risk_tone != "ordinary" else "",
                    "sourceType": alert["sourceType"], "sourceRole": alert["sourceRole"],
                    "sourcePriority": alert["sourcePriority"], "sourceLabel": alert["sourceLabel"],
                    "confirmationStatus": "broker-notice-quotes-exchange-status",
                    "contentHash": hashlib.sha256(alert["body"].encode("utf-8")).hexdigest(),
                    "monitorPeriod": f"提示发布日期次日起至第 {MONITOR_WINDOW_OFFSET_DAYS} 个自然日",
                })
            # 18.cn 页面只展示滚动的最近条目；仅在 18.cn 成功时保留旧窗口，避免主源滚动造成记录消失。
            previous_snapshot = self.repository.active_snapshot() or {}
            previous_items = [
                item for item in previous_snapshot.get("items", [])
                if str(item.get("monitorEndDate") or "") >= (now - timedelta(days=30)).strftime("%Y-%m-%d")
            ]
            merge_items = parsed_events + previous_items if broker_available else parsed_events
            parsed = self._collapse_by_stock(merge_items, now.strftime("%Y-%m-%d"))
            self._cache_active_pdfs(parsed, now.strftime("%Y-%m-%d"))
            updated_at = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
            active_source = "broker_primary" if broker_available else "issuer_disclosure_backup"
            source_label = "18.cn 主源" if broker_available else "巨潮资讯备源（主源降级接替）"
            snapshot = {
                "snapshotId": run_id, "snapshotDate": now.strftime("%Y-%m-%d"),
                "asOfTradeDate": "",
                "startedAt": started_at, "updatedAt": updated_at, "items": parsed,
                "refreshing": False, "error": "",
                "dataQuality": {
                    "source": source_label,
                    "announcementStatus": "source-specific",
                    "verifiedSources": ["东方财富证券 18.cn", "巨潮资讯"],
                    "sourcePolicy": [
                        {"role": "broker_primary", "label": "18.cn 主源", "priority": 1, "status": "available" if broker_available else "degraded"},
                        {"role": "issuer_disclosure_backup", "label": "巨潮资讯备源", "priority": 2, "status": "available" if issuer_available else "degraded"},
                    ],
                    "sourceHealth": {
                        "brokerPrimary": "available" if broker_available else "degraded",
                        "issuerBackup": "available" if issuer_available else "degraded",
                        "brokerError": "" if broker_available else str(broker_result.get("error") or "18.cn 未完整返回"),
                        "issuerError": "" if issuer_available else str(result.get("error") or "巨潮资讯未完整返回"),
                    },
                    "activeSource": active_source,
                    "isOfficialMonitorPeriod": False,
                    "periodCalculation": f"起始日后 {MONITOR_WINDOW_OFFSET_DAYS} 个自然日为结束日；为产品监控口径，非交易所法定期限",
                    "coverage": "18.cn 正常时为主源；18.cn 不可用时由巨潮资讯接替；同源记录按股票和风险类型取最新",
                },
            }
            self.repository.publish(snapshot, parsed_events, run_id)
            self.last_error = ""
            return {"started": True, "refreshing": False, "count": len(parsed)}
        except Exception as exc:
            self.last_error = str(exc)
            self.repository.record_failure(run_id, started_at, self.last_error)
            return {"started": True, "refreshing": False, "error": self.last_error}
        finally:
            self.refreshing = False

    def read(self, status: str, monitor_type: str) -> Dict[str, Any]:
        if status != "current":
            raise ValueError("监控池仅支持 current 当前监控")
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
        today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
        items = []
        for item in snapshot.get("items", []):
            end_date = str(item.get("monitorEndDate") or "")
            if self._is_expired(end_date, today):
                continue
            if monitor_type == "risk" and item.get("riskTone") != "ordinary":
                continue
            if monitor_type == "severe" and item.get("riskTone") not in ("severe-10d", "severe-30d"):
                continue
            if monitor_type in ("ordinary", "severe_10d", "severe_30d"):
                expected = {"ordinary": "ordinary", "severe_10d": "severe-10d", "severe_30d": "severe-30d"}[monitor_type]
                if item.get("riskTone") != expected:
                    continue
            remaining = self._remaining_natural_days(end_date, today)
            row = {**item, "days": remaining}
            if item.get("riskTone") in ("severe-10d", "severe-30d"):
                row["pdfCached"] = bool(self.announcement_pdf_path(str(item.get("monitorKey") or "")))
            items.append(row)
        # 明确监管日期按自然日计算；未公开结束日的记录排在已确认日期之后。
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
