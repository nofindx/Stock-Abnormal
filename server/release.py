"""Container release step: selected additive migration, then a separate API process.

The release operates over the existing configured private MySQL connection.
It does not replay historical schema files or mutate source-market tables.
"""
from pathlib import Path
import os
import sys
from server.services.market_calc_repository import MarketCalcRepository
from server.migrations.runner import parse_migration, apply_migrations


def prepare(repository=None):
    repository = repository or MarketCalcRepository()
    if not repository.available or repository.config.get("database") != "stock_abnormal":
        raise RuntimeError("release database target unavailable or unexpected")
    migration = parse_migration(Path(__file__).parent / "migrations/003_shared_publications.sql")
    with repository.connection() as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 10)", ("stock-abnormal:release:migrations",))
            if not cursor.fetchone()[0]:
                raise RuntimeError("release migration lock unavailable")
        try:
            return apply_migrations(db, [migration])
        finally:
            with db.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", ("stock-abnormal:release:migrations",))


def main():
    try:
        changed = prepare()
        print(f"release schema ready; additive_migrations={len(changed)}", flush=True)
    except Exception as exc:
        # Never expose configured credentials in a driver error.
        print(f"release schema failed: {type(exc).__name__}", flush=True)
        raise SystemExit(1)
    os.execv(sys.executable, [sys.executable, "-m", "server.app"])


if __name__ == "__main__":
    main()
