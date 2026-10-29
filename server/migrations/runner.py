"""Explicit MySQL migration runner.

The application must never call this module during startup or request handling.
Migration SQL is applied only by an operator-controlled command. Statements in
this migration set must be restart-safe because MySQL DDL commits implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import List, Sequence, Tuple


MIGRATION_TABLE = "schema_migration"
_MIGRATION_NAME = re.compile(r"^\d{3,}_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    name: str
    checksum: str
    statements: Tuple[str, ...]


def _strip_sql_comments(sql: str) -> str:
    """Remove line comments used by repository migrations."""

    lines = []
    for line in sql.splitlines():
        if line.lstrip().startswith("--"):
            continue
        lines.append(line)
    return "\n".join(lines)


def parse_migration(path: Path) -> Migration:
    if not _MIGRATION_NAME.fullmatch(path.name):
        raise ValueError(f"非法迁移文件名: {path.name}")
    raw = path.read_bytes()
    text = _strip_sql_comments(raw.decode("utf-8")).strip()
    statements = tuple(part.strip() for part in text.split(";") if part.strip())
    if not statements:
        raise ValueError(f"迁移文件没有 SQL: {path.name}")
    return Migration(path.name, hashlib.sha256(raw).hexdigest(), statements)


def discover_migrations(directory: Path) -> List[Migration]:
    return [parse_migration(path) for path in sorted(directory.glob("*.sql"))]


def apply_migrations(connection, migrations: Sequence[Migration]) -> List[str]:
    """Apply pending migrations and reject edits to already-applied SQL.

    Migration DDL must be idempotent (`CREATE TABLE IF NOT EXISTS`, etc.) so a
    process interruption before the ledger insert can safely replay the file.
    This function does not open connections, read secrets, or run implicitly.
    """

    applied_now: List[str] = []
    with connection.cursor() as cursor:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {MIGRATION_TABLE} (
                migration_name VARCHAR(255) NOT NULL PRIMARY KEY,
                checksum CHAR(64) NOT NULL,
                applied_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        connection.commit()

        for migration in migrations:
            cursor.execute(
                f"SELECT checksum FROM {MIGRATION_TABLE} WHERE migration_name=%s",
                (migration.name,),
            )
            row = cursor.fetchone()
            if row:
                recorded = row[0] if not isinstance(row, dict) else row["checksum"]
                if recorded != migration.checksum:
                    raise ValueError(f"已应用迁移内容被修改: {migration.name}")
                continue

            for statement in migration.statements:
                cursor.execute(statement)
            cursor.execute(
                f"INSERT INTO {MIGRATION_TABLE} (migration_name, checksum) VALUES (%s, %s)",
                (migration.name, migration.checksum),
            )
            connection.commit()
            applied_now.append(migration.name)
    return applied_now


def run_from_directory(connection, directory: Path) -> List[str]:
    """Discover repository migrations and apply them to the supplied DB."""

    migrations = discover_migrations(directory)
    if not migrations:
        raise ValueError("未发现数据库迁移")
    return apply_migrations(connection, migrations)
