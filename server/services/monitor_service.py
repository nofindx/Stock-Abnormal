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
MONITOR_WINDOW_OFFSET_DAYS = 21  # 起始日加 21 个自然日；按用户提供的截图校准，非交易所法定期限。


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
    def _monitor_type(title: str, body: str) -> tuple[str, str]:
        """只按公告明确的类型分类，不用偏离值阈值猜测监管记录类型。"""

        text = re.sub(r"\s+", "", f"{title}\n{body}")
        if "30个交易日" in text or "200%" in text or "300%" in text:
            return "30日严重异动", "severe-30d"
        if "严重异常波动" in text or "10个交易日" in text or "100%" in text or "150%" in text:
            return "10日严重异动", "severe-10d"
        return "风险提示", "ordinary"

    @staticmethod
    def _broker_monitor_type(title: str, body: str) -> tuple[str, str]:
        """券商详情页只有明确严重异常波动措辞才升级，避免脚本数字误触发。"""

        if "严重异常波动" in f"{title} {body}":
            return "10日严重异动", "severe-10d"
        return "风险提示", "ordinary"

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

    def _collapse_by_stock(self, items: List[Dict[str, Any]], snapshot_date: str) -> List[Dict[str, Any]]:
        """合并同股同类型且重叠的窗口；风险提示与严重异动分别保留。"""

        del snapshot_date  # 当前/历史状态在 read 阶段按自然日判定。
        grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault((str(item.get("symbol")), str(item.get("riskTone"))), []).append(item)

        merged_items: List[Dict[str, Any]] = []
        for group in grouped.values():
            group.sort(key=lambda item: (item["monitorStartDate"], item["announcementDate"]))
            periods: List[List[Dict[str, Any]]] = []
            period_end = ""
            for item in group:
                if periods and item["monitorStartDate"] <= period_end:
                    periods[-1].append(item)
                    period_end = max(period_end, item["monitorEndDate"])
                else:
                    periods.append([item])
                    period_end = item["monitorEndDate"]

            for events in periods:
                latest_event = max(events, key=lambda item: item["announcementDate"])
                same_date = [item for item in events if item["announcementDate"] == latest_event["announcementDate"]]
                preferred_event = min(same_date, key=lambda item: int(item.get("sourcePriority", 99)))
                start_date = min(item["monitorStartDate"] for item in events)
                end_date = max(item["monitorEndDate"] for item in events)
                urls = sorted({item["sourceUrl"] for item in events if item.get("sourceUrl")})
                merged_items.append({
                    **preferred_event,
                    "monitorKey": hashlib.sha256("\n".join(sorted(item["sourceId"] for item in events)).encode("utf-8")).hexdigest()[:24],
                    "monitorStart": start_date[5:], "monitorStartDate": start_date,
                    "monitorEnd": end_date[5:], "monitorEndDate": end_date,
                    "daysTotal": (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days + 1,
                    "sourceUrls": urls,
                    "sourceCount": len(urls),
                    "monitorPeriod": f"起始日后 {MONITOR_WINDOW_OFFSET_DAYS} 个自然日；非交易所法定期限",
                })
        return merged_items

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
        try:
            # 覆盖最长 30 个交易日监管窗口及其后 30 个自然日历史留存。
            start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
            result = self.announcements.query_market(start_date, now.strftime("%Y-%m-%d"))
            if not result.get("available") or result.get("partial"):
                raise RuntimeError("公告数据源未完整返回")
            broker_result = self.announcements.query_broker_risk_alerts(start_date, now.strftime("%Y-%m-%d"))
            broker_available = bool(broker_result.get("available"))
            parsed_events: List[Dict[str, Any]] = []
            for announcement in result.get("items", []):
                title = announcement["title"]
                # 一般性的公司风险提示不等于交易所重点监控提示；后者单独使用 18.cn 券商公告源。
                if "异常波动" not in title:
                    continue
                body = self.announcements.extract_pdf_text(announcement["url"])
                monitor_type, risk_tone = self._monitor_type(title, body)
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
                    "source": announcement["source"], "sourceUrl": announcement["url"],
                    "sourceType": "issuer-disclosure", "sourceRole": "issuer_disclosure_backup",
                    "sourcePriority": 3, "sourceLabel": "上市公司法定披露",
                    "confirmationStatus": "issuer-disclosure-confirmed",
                    "contentHash": hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest(),
                    "monitorPeriod": f"公告日起至第 {MONITOR_WINDOW_OFFSET_DAYS} 个自然日",
                })
            for alert in broker_result.get("items", []):
                monitor_start, monitor_end = self._monitor_period(alert["date"], alert["sourceType"])
                # 18.cn 页面脚本可能含无关的百分号数字；只有正文明确出现严重异常波动才升级。
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
                    "source": alert["source"], "sourceUrl": alert["url"],
                    "sourceType": alert["sourceType"], "sourceRole": alert["sourceRole"],
                    "sourcePriority": alert["sourcePriority"], "sourceLabel": alert["sourceLabel"],
                    "confirmationStatus": "broker-notice-quotes-exchange-status",
                    "contentHash": hashlib.sha256(alert["body"].encode("utf-8")).hexdigest(),
                    "monitorPeriod": f"提示发布日期次日起至第 {MONITOR_WINDOW_OFFSET_DAYS} 个自然日",
                })
            # 18.cn 页面只展示滚动的最近条目；保留上次成功快照中的有效窗口，避免来源下滚造成记录消失。
            previous_snapshot = self.repository.active_snapshot() or {}
            previous_items = [
                item for item in previous_snapshot.get("items", [])
                if str(item.get("monitorEndDate") or "") >= (now - timedelta(days=30)).strftime("%Y-%m-%d")
            ]
            parsed = self._collapse_by_stock(parsed_events + previous_items, now.strftime("%Y-%m-%d"))
            updated_at = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
            snapshot = {
                "snapshotId": run_id, "snapshotDate": now.strftime("%Y-%m-%d"),
                "asOfTradeDate": "",
                "startedAt": started_at, "updatedAt": updated_at, "items": parsed,
                "refreshing": False, "error": "",
                "dataQuality": {
                    "source": "东方财富证券 18.cn 重要公告 + 巨潮资讯法定披露公告",
                    "announcementStatus": "source-specific",
                    "verifiedSources": ["东方财富证券 18.cn", "巨潮资讯"],
                    "officialExchangeVerification": "18.cn 为券商风险提示转述；严重异动事实链接上市公司法定披露文件",
                    "sourcePolicy": [
                        {"role": "exchange_official", "label": "交易所官方", "priority": 1, "status": "未接入公开接口"},
                        {"role": "broker_primary", "label": "券商风险提示", "priority": 2, "status": "东方财富证券 18.cn"},
                        {"role": "issuer_disclosure_backup", "label": "上市公司法定披露", "priority": 3, "status": "巨潮资讯"},
                    ],
                    "sourceHealth": {
                        "brokerPrimary": "available" if broker_available else "degraded",
                        "issuerBackup": "available",
                        "brokerError": "" if broker_available else str(broker_result.get("error") or "18.cn 未完整返回"),
                    },
                    "isOfficialMonitorPeriod": False,
                    "periodCalculation": f"起始日后 {MONITOR_WINDOW_OFFSET_DAYS} 个自然日为结束日；为产品监控口径，非交易所法定期限",
                    "coverage": "18.cn 页面当前公开列表与巨潮资讯公开披露；不等于交易所完整重点监控名单",
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
                "tab": status, "items": [], "updatedAt": "", "refreshing": True,
                "error": self.last_error or "今日监控快照尚未生成，后台正在获取",
                "dataQuality": {"source": "公告快照", "isComplete": False},
            }
        # 读取接口只使用快照字段和上海本地日期，不会触发上游请求。
        today = datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")
        items = []
        for item in snapshot.get("items", []):
            end_date = str(item.get("monitorEndDate") or "")
            ended = self._is_history(end_date, today)
            if ended:
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
            items.append({**item, "isHistory": ended, "days": 0 if ended else remaining})
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
    def _is_history(end_date: str, today: str) -> bool:
        """结束日期当天仍为当前监控，次日才进入历史。"""

        return bool(end_date and end_date < today)

    @staticmethod
    def _remaining_natural_days(end_date: str, today: str) -> Optional[int]:
        """按自然日倒计时；来源未给结束日期时不生成猜测值。"""

        if not end_date:
            return None
        return max(0, (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days)
