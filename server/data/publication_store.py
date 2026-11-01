"""Shared MySQL publications; SQLite is an explicit test adapter only.

A successful monitor publication commits facts, pointer and retry work in ONE
transaction. Read payloads are content-addressed: a token cannot relabel latest
data as an older version. No DDL is executed on production request paths.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import sqlite3
import time
import uuid
from typing import Optional
from server.services.market_calc_repository import MarketCalcRepository


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class PublicationUnavailable(RuntimeError):
    pass


class PublicationStore:
    def __init__(self, repository=None, *, sqlite_path=None, clock=time.time):
        self.repository = repository or MarketCalcRepository()
        self.sqlite_path = sqlite_path
        self.clock = clock
        if sqlite_path:
            # Test-only DDL. Production always uses the explicit SQL migration.
            with self.connection() as db:
                db.executescript("""
                CREATE TABLE IF NOT EXISTS publication_record (
                  publication_id TEXT PRIMARY KEY, publication_kind TEXT,
                  content_hash TEXT, payload TEXT, expires_at REAL);
                CREATE TABLE IF NOT EXISTS publication_state (state_key TEXT PRIMARY KEY, state_value TEXT);
                CREATE TABLE IF NOT EXISTS publication_fact (
                  revision_id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT,
                  content_hash TEXT, symbol TEXT, announcement_date TEXT, pending INTEGER,
                  payload TEXT, UNIQUE(source_id, content_hash));
                CREATE TABLE IF NOT EXISTS publication_outbox (
                  publication_id TEXT PRIMARY KEY, attempt INTEGER DEFAULT 0,
                  delivered INTEGER DEFAULT 0, last_error TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS publication_refresh_job (
                  job_id TEXT PRIMARY KEY, request_hash TEXT, status TEXT,
                  owner TEXT DEFAULT '', lease_until REAL DEFAULT 0,
                  expires_at REAL, result_view_id TEXT DEFAULT '');
                """)

    @contextmanager
    def connection(self):
        if self.sqlite_path:
            db = sqlite3.connect(self.sqlite_path, timeout=10)
            try:
                yield db
            finally:
                db.close()
        else:
            with self.repository.connection() as db:
                yield db

    def execute(self, db, sql, args=()):
        if self.sqlite_path:
            return db.execute(sql.replace("%s", "?"), args)
        cursor = db.cursor()
        cursor.execute(sql, args)
        return cursor

    def insert_ignore(self, db, table, columns, values):
        verb = "INSERT OR IGNORE" if self.sqlite_path else "INSERT IGNORE"
        return self.execute(db, f"{verb} INTO {table} ({columns}) VALUES ({','.join(['%s'] * len(values))})", values)

    def _set_state(self, db, key, value):
        if self.sqlite_path:
            sql = "INSERT INTO publication_state VALUES (%s,%s) ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value"
        else:
            sql = "INSERT INTO publication_state VALUES (%s,%s) ON DUPLICATE KEY UPDATE state_value=VALUES(state_value)"
        self.execute(db, sql, (key, str(value)))

    def set_state(self, key, value):
        with self.connection() as db:
            self._set_state(db, key, value)
            db.commit()

    def states(self):
        with self.connection() as db:
            return dict(self.execute(db, "SELECT state_key,state_value FROM publication_state").fetchall())

    @contextmanager
    def locked(self, name):
        with self.connection() as db:
            acquired = False
            try:
                if self.sqlite_path:
                    db.execute("BEGIN IMMEDIATE")
                else:
                    acquired = bool(self.execute(db, "SELECT GET_LOCK(%s, 1)", (name,)).fetchone()[0])
                    if not acquired:
                        raise PublicationUnavailable("shared publication is busy")
                yield db
            except Exception:
                db.rollback()
                raise
            finally:
                if acquired:
                    self.execute(db, "SELECT RELEASE_LOCK(%s)", (name,))

    def _put(self, db, publication_id, kind, payload, expires=None):
        raw = canonical(payload)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        self.insert_ignore(db, "publication_record", "publication_id,publication_kind,content_hash,payload,expires_at",
                           (publication_id, kind, digest, raw, expires))
        row = self.execute(db, "SELECT content_hash FROM publication_record WHERE publication_id=%s", (publication_id,)).fetchone()
        if not row or row[0] != digest:
            raise PublicationUnavailable("immutable publication collision")
        if expires is not None:
            self.execute(db, "UPDATE publication_record SET expires_at=%s WHERE publication_id=%s AND expires_at<%s",
                         (expires, publication_id, expires))

    def get(self, publication_id, kind=None):
        with self.connection() as db:
            row = self.execute(db, "SELECT publication_kind,payload,expires_at FROM publication_record WHERE publication_id=%s",
                               (publication_id,)).fetchone()
        if not row or (kind and row[0] != kind) or (row[2] is not None and row[2] <= self.clock()):
            raise PublicationUnavailable("authorized publication is unavailable or expired")
        return json.loads(row[1]) if isinstance(row[1], str) else row[1]

    def freeze_read(self, payload, scope, ttl=600):
        raw = canonical({"scope": scope, "data": payload})
        identity = hashlib.sha256(raw.encode()).hexdigest()
        with self.connection() as db:
            self._put(db, identity, "read", payload, self.clock() + ttl)
            db.commit()
        return identity

    def entry_context(self, entry_id, fingerprint, context, ttl):
        identity = hashlib.sha256(("entry:" + entry_id).encode()).hexdigest()
        value = {"fingerprint": fingerprint, "context": context}
        with self.locked("stock-abnormal:read:entries") as db:
            row = self.execute(db, "SELECT payload,expires_at FROM publication_record WHERE publication_id=%s", (identity,)).fetchone()
            if row and row[1] > self.clock():
                db.rollback()
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]
            if row:
                self.execute(db, "DELETE FROM publication_record WHERE publication_id=%s AND expires_at<=%s", (identity, self.clock()))
            self._put(db, identity, "entry", value, self.clock() + ttl)
            db.commit()
        return value

    def prune_expired_reads(self):
        with self.connection() as db:
            self.execute(db, "DELETE FROM publication_record WHERE publication_kind IN ('read','entry') AND expires_at<%s", (self.clock() - 300,))
            self.execute(db, "DELETE FROM publication_refresh_job WHERE expires_at<%s", (self.clock() - 300,))
            db.commit()

    def refresh_job(self, context, job_id=None, *, claim=False):
        """Authorize/claim a shared S2 job; a changed owner fences late work."""
        request_hash = hashlib.sha256((context.dataset_id + ':' + context.query_hash).encode()).hexdigest()
        create = job_id is None
        if create:
            job_id = hashlib.sha256((request_hash + ':' + str(int(self.clock() // 3))).encode()).hexdigest()
        with self.connection() as db:
            if self.sqlite_path:
                db.execute("BEGIN IMMEDIATE")
            if create:
                self.insert_ignore(db, "publication_refresh_job", "job_id,request_hash,status,expires_at",
                                   (job_id, request_hash, "queued", context.expires_at))
            suffix = "" if self.sqlite_path else " FOR UPDATE"
            row = self.execute(db, "SELECT request_hash,status,owner,lease_until,expires_at,result_view_id FROM publication_refresh_job WHERE job_id=%s" + suffix,
                               (job_id,)).fetchone()
            if not row or row[0] != request_hash or row[4] <= self.clock():
                raise PublicationUnavailable("refresh job scope expired or unavailable")
            owner = ""
            if claim and (row[1] == "queued" or row[1] == "running" and row[3] <= self.clock()):
                owner = uuid.uuid4().hex
                self.execute(db, "UPDATE publication_refresh_job SET status='running',owner=%s,lease_until=%s WHERE job_id=%s",
                             (owner, self.clock() + 30, job_id))
            db.commit()
        return {"job_id": job_id, "status": "running" if owner else row[1], "owner": owner,
                "view_id": row[5], "expires_at": row[4]}

    def complete_refresh_job(self, identity, owner, view_id):
        with self.connection() as db:
            cursor = self.execute(db, "UPDATE publication_refresh_job SET status='ready',result_view_id=%s,lease_until=0 WHERE job_id=%s AND owner=%s AND expires_at>%s",
                                  (view_id, identity, owner, self.clock()))
            updated = cursor.rowcount
            db.commit()
        return bool(updated)

    def active_snapshot(self):
        identity = self.states().get("active_snapshot_id")
        return self.get(identity, "monitor") if identity else None

    def publish(self, snapshot, announcements, run_id):
        with self.locked("stock-abnormal:monitor:publish") as db:
            pointer = self.execute(db, "SELECT state_value FROM publication_state WHERE state_key='active_snapshot_id'").fetchone()
            # An older worker finishing late may not replace a newer snapshot.
            if pointer and str(pointer[0]) > str(snapshot["snapshotId"]):
                db.rollback()
                return False
            for pending, values in ((0, announcements), (1, snapshot.get("unresolvedFacts") or [])):
                for item in values:
                    source_id = str(item.get("sourceId") or "")
                    if not source_id:
                        continue
                    raw = canonical(item)
                    self.insert_ignore(db, "publication_fact",
                        "source_id,content_hash,symbol,announcement_date,pending,payload",
                        (source_id, hashlib.sha256(raw.encode()).hexdigest(),
                         str(item.get("symbol") or "").split(".")[0],
                         str(item.get("announcementDate") or "").replace("-", "")[:8], pending, raw))
            self._put(db, snapshot["snapshotId"], "monitor", snapshot)
            self._set_state(db, "active_snapshot_id", snapshot["snapshotId"])
            self._set_state(db, "last_status", "success")
            self._set_state(db, "last_failure_date", "")
            self.insert_ignore(db, "publication_outbox", "publication_id", (snapshot["snapshotId"],))
            db.commit()
        return True

    def rebuild_facts(self, symbol=None, since=None, *, pending=False):
        # Prefer the last confirmed revision over any temporary failed re-read.
        clauses, args = ["p.pending=%s"], [int(pending)]
        if symbol:
            clauses.append("p.symbol=%s"); args.append(str(symbol).split(".")[0])
        if since:
            clauses.append("p.announcement_date>=%s"); args.append(str(since).replace("-", ""))
        sql = """SELECT p.payload FROM publication_fact p JOIN (
          SELECT source_id,MAX(revision_id) AS r FROM publication_fact WHERE pending=%s GROUP BY source_id
        ) latest ON latest.r=p.revision_id WHERE """ + " AND ".join(clauses)
        with self.connection() as db:
            rows = self.execute(db, sql, tuple([int(pending)] + args)).fetchall()
        return [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in rows]

    def dispatch(self, callback):
        if not callback:
            return False
        with self.locked("stock-abnormal:monitor:outbox") as db:
            row = self.execute(db, "SELECT publication_id FROM publication_outbox WHERE delivered=0 ORDER BY publication_id LIMIT 1").fetchone()
            if not row:
                db.rollback()
                return False
            identity = row[0]
            try:
                if callback() is False:
                    raise PublicationUnavailable("derived dataset rebuild not ready")
            except Exception as exc:
                self.execute(db, "UPDATE publication_outbox SET attempt=attempt+1,last_error=%s WHERE publication_id=%s",
                             (type(exc).__name__, identity))
                db.commit()
                return False
            self.execute(db, "UPDATE publication_outbox SET attempt=attempt+1,delivered=1,last_error='' WHERE publication_id=%s", (identity,))
            db.commit()
            return True

    def record_failure(self, run_id, started_at, message):
        with self.connection() as db:
            self._set_state(db, "last_status", "failed")
            self._set_state(db, "last_failure_date", started_at[:10])
            db.commit()

    def health(self):
        with self.connection() as db:
            facts = self.execute(db, "SELECT COUNT(DISTINCT source_id) FROM publication_fact WHERE pending=0").fetchone()[0]
            backlog = self.execute(db, "SELECT COUNT(*) FROM publication_outbox WHERE delivered=0").fetchone()[0]
        return {"available": True, "backend": "sqlite-test" if self.sqlite_path else "mysql",
                "factCount": facts, "outboxPending": backlog}

    @property
    def fact_ledger(self):
        return self

    def rebuild(self, symbol=None, since=None):
        return self.rebuild_facts(symbol, since)

    def pending(self, symbol=None):
        confirmed = {x.get("sourceId") for x in self.rebuild_facts(symbol)}
        return [x for x in self.rebuild_facts(symbol, pending=True) if x.get("sourceId") not in confirmed]
