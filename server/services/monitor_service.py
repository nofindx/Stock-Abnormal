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

from .announcement_service import AnnouncementService
from .tushare_client import TushareClient

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone(timedelta(hours=8))


class MonitorRepository:
    """用事务保存公告原文和监管池快照，失败任务不会覆盖活动快照。"""

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
            db.execute("INSERT OR REPLACE INTO ingest_run VALUES (?, ?, ?, 'success', ?, '')", (
                run_id, snapshot["startedAt"], snapshot["updatedAt"], len(snapshot["items"]),
            ))
            db.commit()

    def record_failure(self, run_id: str, started_at: str, message: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO ingest_run VALUES (?, ?, ?, 'failed', 0, ?)", (
                run_id, started_at, datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M"), message,
            ))


class OfficialMonitorService:
    """采集公开披露公告并生成监管池快照，不读取全市场交易行情。"""

    def __init__(self, client: TushareClient, announcements: Optional[AnnouncementService] = None) -> None:
        self.client = client
        self.announcements = announcements or AnnouncementService()
        self.repository = MonitorRepository()
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
        # 启动后先读快照，不抢占 HTTP 健康检查；到点后执行一次，失败在窗口内继续重试。
        self._scheduler_stop.wait(0.5)
        while not self._scheduler_stop.is_set():
            try:
                now = datetime.now(SHANGHAI_TZ)
                latest = self.repository.active_snapshot()
                snapshot_date = str(latest.get("snapshotDate", "")) if latest else ""
                due = not latest or snapshot_date != now.strftime("%Y-%m-%d")
                # 00:05 后生成；失败只在当天确认窗口内重试，避免全天打满公告源。
                after_midnight = (now.hour, now.minute) >= (0, 5)
                before_confirm_end = (now.hour, now.minute) < (8, 30)
                if due and ((after_midnight and before_confirm_end) or not latest):
                    self.refresh()
            except Exception as exc:  # noqa: BLE001 - 调度线程必须保持存活
                self.last_error = str(exc)
            self._scheduler_stop.wait(600)

    @staticmethod
    def _monitor_type(title: str, body: str) -> tuple[str, str, int]:
        text = re.sub(r"\s+", "", f"{title}\n{body}")
        if "30个交易日" in text or "200%" in text or "300%" in text:
            return "30日严重异动", "severe-30d", 30
        if "严重异常波动" in text or "10个交易日" in text or "100%" in text or "150%" in text:
            return "10日严重异动", "severe-10d", 10
        return "风险提示", "ordinary", 3

    def _future_open_dates(self, start_date: str, count: int) -> List[str]:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = start + timedelta(days=60)
        frame = self.client.trade_cal(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        return [str(row["cal_date"]) for row in sorted(frame.to_dict("records"), key=lambda row: str(row["cal_date"])) if int(row.get("is_open", 0)) == 1][:count]

    @staticmethod
    def _risk_rank(item: Dict[str, Any]) -> int:
        return {"severe-30d": 3, "severe-10d": 2, "ordinary": 1}.get(str(item.get("riskTone")), 0)

    def _collapse_by_stock(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """同一股票有多条披露时只保留最新且风险等级最高的一条，避免卡片重复。"""

        selected: Dict[str, Dict[str, Any]] = {}
        for item in items:
            key = str(item.get("symbol") or item.get("monitorKey"))
            previous = selected.get(key)
            if not previous or (
                self._risk_rank(item), str(item.get("announcementDate", ""))
            ) > (
                self._risk_rank(previous), str(previous.get("announcementDate", ""))
            ):
                selected[key] = item
        return list(selected.values())

    def refresh(self) -> Dict[str, Any]:
        """后台调用：公告源完整可用且快照校验通过后才原子发布。"""

        with self._lock:
            if self.refreshing:
                return {"started": False, "refreshing": True}
            self.refreshing = True
        now = datetime.now(SHANGHAI_TZ)
        started_at = now.strftime("%Y-%m-%d %H:%M")
        run_id = now.strftime("%Y%m%d%H%M%S")
        try:
            start_date = (now - timedelta(days=45)).strftime("%Y-%m-%d")
            latest_trade_date = self.client.latest_trade_date()
            result = self.announcements.query_market(start_date, now.strftime("%Y-%m-%d"))
            if not result.get("available") or result.get("partial"):
                raise RuntimeError("公告数据源未完整返回")
            parsed: List[Dict[str, Any]] = []
            for announcement in result.get("items", []):
                title = announcement["title"]
                body = self.announcements.extract_pdf_text(announcement["url"]) if "严重异常波动" in title else ""
                monitor_type, risk_tone, window_days = self._monitor_type(title, body)
                if not announcement.get("date"):
                    continue
                dates = self._future_open_dates(announcement["date"], window_days)
                end_date = dates[-1] if dates else ""
                remaining = len([value for value in dates if value > latest_trade_date])
                source_key = announcement["url"] or f"{announcement['stockCode']}:{announcement['date']}:{title}"
                source_id = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:24]
                parsed.append({
                    "sourceId": source_id, "monitorKey": source_id,
                    "symbol": announcement["stockCode"], "name": announcement["stockName"],
                    "title": title, "announcementDate": announcement["date"],
                    "monitorStart": announcement["date"][5:],
                    "monitorEnd": f"{end_date[4:6]}-{end_date[6:8]}" if end_date else "待确认",
                    "monitorEndDate": end_date, "days": remaining, "daysTotal": window_days,
                    "monitorDates": dates,
                    "monitorType": monitor_type, "riskTone": risk_tone,
                    "isST": "ST" in announcement["stockName"].upper(),
                    "source": announcement["source"], "sourceUrl": announcement["url"],
                    "confirmationStatus": "designated-disclosure-confirmed",
                    "contentHash": hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest(),
                    "monitorPeriod": "按公告日和规则窗口推算",
                })
            parsed = self._collapse_by_stock(parsed)
            if not parsed:
                raise RuntimeError("公告源返回空结果，拒绝覆盖旧快照")
            updated_at = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
            snapshot = {
                "snapshotId": run_id, "snapshotDate": now.strftime("%Y-%m-%d"),
                "asOfTradeDate": latest_trade_date,
                "startedAt": started_at, "updatedAt": updated_at, "items": parsed,
                "refreshing": False, "error": "",
                "dataQuality": {
                    "source": "巨潮资讯公开披露公告",
                    "announcementStatus": "designated-disclosure-confirmed",
                    "verifiedSources": ["巨潮资讯"],
                    "officialExchangeVerification": "未接入交易所官方名单接口",
                    "isOfficialMonitorPeriod": False,
                    "coverage": "公开披露公告，不含非公开重点监控名单",
                },
            }
            self.repository.publish(snapshot, parsed, run_id)
            self.last_error = ""
            return {"started": True, "refreshing": False, "count": len(parsed)}
        except Exception as exc:
            self.last_error = str(exc)
            self.repository.record_failure(run_id, started_at, self.last_error)
            return {"started": True, "refreshing": False, "error": self.last_error}
        finally:
            self.refreshing = False

    def read(self, status: str, monitor_type: str) -> Dict[str, Any]:
        if status not in ("current", "history"):
            raise ValueError("status 必须是 current 或 history")
        snapshot = self.repository.active_snapshot()
        if not snapshot:
            return {
                "tab": status, "items": [], "updatedAt": "", "refreshing": True,
                "error": self.last_error or "今日监控快照尚未生成，后台正在获取",
                "dataQuality": {"source": "公告快照", "isComplete": False},
            }
        # 读取接口只使用快照字段，不能为了判断当前/历史再次访问行情源。
        latest = str(snapshot.get("asOfTradeDate") or "")
        items = []
        for item in snapshot.get("items", []):
            ended = bool(item.get("monitorEndDate") and item["monitorEndDate"] < latest)
            if (status == "history") != ended:
                continue
            if monitor_type == "risk" and item.get("riskTone") != "ordinary":
                continue
            if monitor_type == "severe" and item.get("riskTone") not in ("severe-10d", "severe-30d"):
                continue
            if monitor_type in ("ordinary", "severe_10d", "severe_30d"):
                expected = {"ordinary": "ordinary", "severe_10d": "severe-10d", "severe_30d": "severe-30d"}[monitor_type]
                if item.get("riskTone") != expected:
                    continue
            remaining = len([value for value in item.get("monitorDates", []) if value > latest])
            items.append({**item, "isHistory": ended, "days": 0 if ended else remaining})
        # 当前监控按剩余交易日升序；历史按公告日期倒序，便于先看最近结束的记录。
        if status == "current":
            items.sort(key=lambda item: (item.get("days", 999), item.get("announcementDate", "")))
        else:
            items.sort(key=lambda item: str(item.get("announcementDate", "")), reverse=True)
        return {
            **snapshot,
            "tab": status,
            "items": items,
            "refreshing": self.refreshing,
            "error": self.last_error or snapshot.get("error", ""),
        }
