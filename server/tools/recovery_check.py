"""Read-only T2 inventory/diff. Never imports the API or runs schema DDL.

Use T2_MYSQL_* variables explicitly; production MYSQL_* is never inherited.
Inventories contain hashes and counts, not row contents or credentials.
Equal inventories prove equality at the captured point, not backup freshness,
API compatibility, replay correctness, RPO or RTO.
"""
import argparse
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def encode(value):
    if isinstance(value, (datetime, date)):
        return {"datetime": value.isoformat()}
    if isinstance(value, timedelta):
        return {"microseconds": round(value.total_seconds() * 1000000)}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    raise TypeError(type(value).__name__)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=encode, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def table_digest(rows, json_columns=()):
    """Multiset digest: independent of row order; preserves duplicate rows."""
    hashes = []
    for row in rows:
        row = dict(row)
        for key in json_columns:
            if isinstance(row.get(key), (str, bytes)):
                row[key] = json.loads(row[key])
        hashes.append(digest(row))
    return {"rowCount": len(hashes), "contentHash": digest(sorted(hashes))}


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError("unsupported database/table identifier")
    return "`" + value + "`"


def inventory(db):
    """One repeatable-read InnoDB snapshot; no DDL or business writes.

    Freeze DDL externally during backup/inventory. Cross-table consistency is
    guaranteed only for transactional InnoDB tables, so reject other engines.
    """
    result = {"format": "T2-INVENTORY-1", "tables": {}}
    try:
        with db.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            cur.execute("SELECT DATABASE(), VERSION(), UTC_TIMESTAMP(6)")
            database, version, captured = cur.fetchone()
            result.update(database=database, serverVersion=version, capturedAtUtc=str(captured))
            cur.execute("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME", (database,))
            tables = cur.fetchall()
            if not tables:
                raise ValueError("database has no base tables; empty inventory is not evidence")
            if any(engine != "InnoDB" for _, engine in tables):
                raise ValueError("non-InnoDB tables require a separate consistent-backup procedure")
            for name, _ in tables:
                cur.execute("SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                            "EXTRA, COLLATION_NAME, GENERATION_EXPRESSION FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION", (database, name))
                columns = cur.fetchall()
                json_columns = [x[0] for x in columns if x[1].lower() == "json"]
                cur.execute("SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART, INDEX_TYPE "
                            "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                            "ORDER BY INDEX_NAME,SEQ_IN_INDEX", (database, name))
                indexes = cur.fetchall()
                cur.execute("SELECT CONSTRAINT_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME "
                            "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                            "ORDER BY CONSTRAINT_NAME,ORDINAL_POSITION", (database, name))
                constraints = cur.fetchall()
                cur.execute("SELECT CONSTRAINT_NAME,UPDATE_RULE,DELETE_RULE "
                            "FROM information_schema.REFERENTIAL_CONSTRAINTS "
                            "WHERE CONSTRAINT_SCHEMA=%s AND TABLE_NAME=%s ORDER BY CONSTRAINT_NAME", (database, name))
                foreign_key_rules = cur.fetchall()
                cur.execute("SELECT * FROM " + identifier(name))
                names = [x[0] for x in cur.description]
                rows = (dict(zip(names, row)) for row in cur.fetchall())
                summary = table_digest(rows, json_columns)
                summary["schemaHash"] = digest([columns, indexes, constraints, foreign_key_rules])
                result["tables"][name] = summary
        result["rootHash"] = digest(result["tables"])
        return result
    finally:
        db.rollback()


def validate_inventory(value):
    if value.get("format") != "T2-INVENTORY-1" or not value.get("tables"):
        raise ValueError("missing or unsupported inventory")
    for name, table in value["tables"].items():
        identifier(name)
        if (type(table.get("rowCount")) is not int or table["rowCount"] < 0
                or any(not re.fullmatch(r"[a-f0-9]{64}", table.get(k, ""))
                       for k in ("contentHash", "schemaHash"))):
            raise ValueError("invalid table inventory")
    if value.get("rootHash") != digest(value["tables"]):
        raise ValueError("inventory root hash mismatch")


def compare(before, after):
    validate_inventory(before)
    validate_inventory(after)
    changes = []
    for name in sorted(set(before["tables"]) | set(after["tables"])):
        left, right = before["tables"].get(name), after["tables"].get(name)
        if left != right:
            changes.append({"table": name, "before": left, "after": right})
    return {"status": "pass" if not changes else "fail", "differences": changes,
            "scope": "exact schema/count/content equality only; not full T2 acceptance"}


def recovery_timing(backup_point, incident_at, started_at, recovered_at):
    """UTC-aware inputs; backup start time must not masquerade as recovery point."""
    values = [datetime.fromisoformat(x.replace("Z", "+00:00"))
              for x in (backup_point, incident_at, started_at, recovered_at)]
    if any(x.tzinfo is None for x in values):
        raise ValueError("timestamps require timezone")
    backup, incident, start, end = values
    if not backup <= incident <= start <= end:
        raise ValueError("invalid recovery timeline")
    rpo, rto = (incident - backup).total_seconds(), (end - incident).total_seconds()
    return {"rpoSeconds": rpo, "rtoSeconds": rto,
            "executionSeconds": (end - start).total_seconds(),
            "status": "pass" if rpo <= 86400 and rto <= 7200 else "fail"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--expect-database", required=True)
    snap.add_argument("--output", type=Path, required=True)
    diff = sub.add_parser("compare")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "compare":
            result = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "pass" else 1
        config = {key: os.environ.get("T2_MYSQL_" + key.upper(), "")
                  for key in ("host", "user", "password", "database")}
        if not all(config.values()) or config["database"] != args.expect_database:
            raise ValueError("T2_MYSQL_* incomplete or database mismatch")
        identifier(config["database"])
        config.update(port=int(os.environ.get("T2_MYSQL_PORT", "3306")),
                      charset="utf8mb4", autocommit=False, connect_timeout=5,
                      read_timeout=120, write_timeout=30)
        # Exclusive creation prevents accidentally replacing previous evidence.
        with args.output.open("x", encoding="utf-8") as output:
            os.chmod(args.output, 0o600)
            import pymysql
            db = pymysql.connect(**config)
            try:
                result = inventory(db)
            finally:
                db.close()
            json.dump(result, output, ensure_ascii=False, indent=2)
        print("Read-only inventory saved; tables=" + str(len(result["tables"])))
        return 0
    except Exception as exc:
        # Driver exceptions can contain connection settings. Do not echo them.
        print("T2 operation incomplete: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
