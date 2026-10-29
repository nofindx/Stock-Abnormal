"""Operator-invoked migration command; never imported by the API process."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from server.migrations.runner import run_from_directory


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply explicit vNext MySQL migrations")
    parser.add_argument("--apply", action="store_true", help="required confirmation to execute DDL")
    parser.add_argument("--expect-database", required=True, help="must exactly match MYSQL_DATABASE")
    args = parser.parse_args()
    if not args.apply:
        parser.error("只读模式；执行 DDL 必须显式传入 --apply")

    config = {
        "host": os.getenv("MYSQL_HOST") or os.getenv("MYSQL_ADDRESS", ""),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER") or os.getenv("MYSQL_USERNAME", ""),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv("MYSQL_DATABASE") or os.getenv("MYSQL_DB", "stock_abnormal"),
        "charset": "utf8mb4",
        "connect_timeout": 5,
        "autocommit": False,
    }
    if not config["host"] or not config["user"] or not config["password"]:
        parser.error("MySQL 连接配置不完整")
    if config["database"] != args.expect_database:
        parser.error("--expect-database 与实际 MYSQL_DATABASE 不匹配，拒绝执行")

    try:
        import pymysql
    except ImportError as exc:
        parser.error(f"缺少 PyMySQL 驱动: {exc}")

    connection = pymysql.connect(**config)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()[0]
        migrations = Path(__file__).resolve().parent
        applied = run_from_directory(connection, migrations)
        print(f"MySQL migration run complete; server={version}; newly_applied={len(applied)}")
        for name in applied:
            print(name)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
