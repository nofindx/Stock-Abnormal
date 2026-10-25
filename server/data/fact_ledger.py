"""可审计的公告事实账本。

公告抓取、正文解析和行情规则计算是三个不同阶段。此模块只保存解析后的
事实契约及来源证据，不参与阈值计算；重复抓取同一来源不会重复计次，且
可以从账本按股票/日期重建输入，供历史回放和规则版本迁移使用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class AnnouncementFact:
    source_id: str
    symbol: str
    announcement_date: str
    fact_type: str
    direction: str = ""
    event_date: str = ""
    rule_reset_date: str = ""
    source_type: str = ""
    source_url: str = ""
    title: str = ""
    body_hash: str = ""
    confirmation_status: str = ""
    payload: Dict[str, Any] = None  # type: ignore[assignment]

    def to_record(self) -> Dict[str, Any]:
        record = asdict(self)
        record["payload"] = self.payload or {}
        return record


class FactLedger:
    """SQLite-backed, idempotent fact store.

    ``source_id`` is the immutable deduplication key. The ledger intentionally
    retains facts across snapshot replacement; a failed or partial source read
    therefore cannot erase the evidence required to rebuild a calculation.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS announcement_fact (
                    source_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    announcement_date TEXT NOT NULL,
                    fact_type TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT '',
                    event_date TEXT NOT NULL DEFAULT '',
                    rule_reset_date TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    body_hash TEXT NOT NULL DEFAULT '',
                    confirmation_status TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS announcement_pending (
                    source_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    announcement_date TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                )
            """)
            db.commit()

    @staticmethod
    def _fact_type(item: Dict[str, Any]) -> str:
        return str(item.get("factType") or item.get("riskTone") or item.get("monitorType") or "unknown")

    @staticmethod
    def _source_id(item: Dict[str, Any]) -> str:
        source_id = str(item.get("sourceId") or item.get("source_id") or "").strip()
        if source_id:
            return source_id
        material = "|".join(str(item.get(key) or "") for key in ("symbol", "announcementDate", "title", "sourceUrl"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    def upsert(self, facts: Iterable[Dict[str, Any] | AnnouncementFact]) -> int:
        rows = []
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for value in facts:
            item = value.to_record() if isinstance(value, AnnouncementFact) else dict(value)
            item["sourceId"] = self._source_id(item)
            rows.append((
                item["sourceId"], str(item.get("symbol") or ""), str(item.get("announcementDate") or ""),
                self._fact_type(item), str(item.get("eventDirection") or item.get("direction") or ""),
                str(item.get("eventDate") or ""), str(item.get("ruleResetDate") or ""),
                str(item.get("sourceType") or ""), str(item.get("sourceUrl") or ""),
                str(item.get("title") or ""), str(item.get("contentHash") or item.get("bodyHash") or ""),
                str(item.get("confirmationStatus") or ""), json.dumps(item, ensure_ascii=False, separators=(",", ":")),
                recorded_at,
            ))
        if not rows:
            return 0
        with self._lock, self._connect() as db:
            db.executemany("""
                INSERT INTO announcement_fact
                (source_id, symbol, announcement_date, fact_type, direction, event_date,
                 rule_reset_date, source_type, source_url, title, body_hash,
                 confirmation_status, payload, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  payload=excluded.payload, confirmation_status=excluded.confirmation_status,
                  body_hash=excluded.body_hash, recorded_at=excluded.recorded_at
            """, rows)
            # 正式事实已成功写入时，清理同一来源的待核验占位，避免
            # pendingCount 因一次历史正文读取失败永久累积。
            db.executemany(
                "DELETE FROM announcement_pending WHERE source_id = ?",
                [(row[0],) for row in rows],
            )
            db.commit()
        return len(rows)

    def list(self, symbol: Optional[str] = None, since: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if symbol:
            clauses.append("symbol = ?")
            params.append(str(symbol).split(".")[0])
        if since:
            clauses.append("announcement_date >= ?")
            params.append(str(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT payload FROM announcement_fact {where} ORDER BY announcement_date, source_id", params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def upsert_pending(self, facts: Iterable[Dict[str, Any]]) -> int:
        """保存官方命中但正文/事实尚未核验的记录，不把它计入正式账本。"""
        rows = []
        recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for value in facts:
            item = dict(value)
            source_id = self._source_id(item)
            rows.append((
                source_id, str(item.get("symbol") or ""), str(item.get("announcementDate") or ""),
                str(item.get("sourceUrl") or ""), str(item.get("title") or ""),
                str(item.get("status") or "pending_verification"), str(item.get("reason") or ""),
                json.dumps({**item, "sourceId": source_id}, ensure_ascii=False, separators=(",", ":")),
                recorded_at,
            ))
        if not rows:
            return 0
        with self._lock, self._connect() as db:
            db.executemany("""
                INSERT INTO announcement_pending
                (source_id, symbol, announcement_date, source_url, title, status, reason, payload, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  status=excluded.status, reason=excluded.reason,
                  payload=excluded.payload, recorded_at=excluded.recorded_at
            """, rows)
            db.commit()
        return len(rows)

    def pending(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses, params = [], []
        if symbol:
            clauses.append("symbol = ?")
            params.append(str(symbol).split(".")[0])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(f"SELECT payload FROM announcement_pending {where} ORDER BY announcement_date, source_id", params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def rebuild(self, symbol: Optional[str] = None, since: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return deterministic, deduplicated facts for a calculation rebuild."""
        facts = self.list(symbol=symbol, since=since)
        unique: Dict[str, Dict[str, Any]] = {}
        for item in facts:
            unique[self._source_id(item)] = item
        return sorted(unique.values(), key=lambda item: (
            str(item.get("announcementDate") or ""), self._source_id(item),
        ))

    def health(self) -> Dict[str, Any]:
        with self._connect() as db:
            count = int(db.execute("SELECT COUNT(*) FROM announcement_fact").fetchone()[0])
            pending_count = int(db.execute("SELECT COUNT(*) FROM announcement_pending").fetchone()[0])
        return {"available": True, "factCount": count, "pendingCount": pending_count, "path": str(self.path)}
