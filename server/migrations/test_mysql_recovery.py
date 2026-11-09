"""Opt-in real MySQL migration drill; never falls back to MYSQL_* credentials.

Requires an EMPTY, dedicated t2_* schema and T2_RUN_MYSQL_DRILL=1.
Leaves all evidence tables in that schema for inspection; does not drop data.
This is not a production backup restore or an RPO/RTO measurement.
"""
import os
from pathlib import Path
import unittest

from server.migrations.runner import apply_migrations, discover_migrations
from server.tools.recovery_check import inventory, compare


class InjectedDisconnect(RuntimeError):
    pass


class InterruptedCursor:
    def __init__(self, connection, cursor, sql):
        self.connection, self.cursor, self.sql = connection, cursor, sql

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, sql, params=None):
        result = self.cursor.execute(sql, params)
        if sql == self.sql:
            # MySQL DDL has already committed. Close before the ledger commit.
            self.connection.close()
            raise InjectedDisconnect()
        return result

    def fetchone(self):
        return self.cursor.fetchone()


class InterruptedConnection:
    def __init__(self, connection, sql):
        self.connection, self.sql = connection, sql

    def cursor(self):
        return InterruptedCursor(self.connection, self.connection.cursor(), self.sql)

    def commit(self):
        return self.connection.commit()


@unittest.skipUnless(os.getenv("T2_RUN_MYSQL_DRILL") == "1", "real isolated MySQL not configured")
class MySQLRecoveryDrill(unittest.TestCase):
    def test_real_ddl_disconnect_reconnect_retry_and_publication_transaction(self):
        import pymysql
        from server.data.publication_store import PublicationStore, PublicationUnavailable
        from server.services.market_calc_repository import MarketCalcRepository

        config = {key: os.getenv("T2_MYSQL_" + key.upper(), "")
                  for key in ("host", "user", "password", "database")}
        self.assertTrue(all(config.values()), "T2-only connection settings are required")
        self.assertRegex(config["database"], r"^t2_[a-z0-9_]+$")
        config.update(port=int(os.getenv("T2_MYSQL_PORT", "3306")), charset="utf8mb4",
                      autocommit=False, connect_timeout=5)

        def connect():
            return pymysql.connect(**config)

        db = connect()
        try:
            with db.cursor() as cur:
                cur.execute("SELECT DATABASE()")
                self.assertEqual(cur.fetchone()[0], config["database"])
                cur.execute("SHOW FULL TABLES")
                self.assertEqual(cur.fetchall(), (), "dedicated EMPTY schema required; nothing will be deleted")
                cur.execute("CREATE TABLE t2_preserved_input (id INT PRIMARY KEY, payload JSON NOT NULL) ENGINE=InnoDB")
                cur.execute("INSERT INTO t2_preserved_input VALUES (1, %s)", ('{"event":"ordinary","effective_date":"20261109"}',))
            db.commit()
            migrations = discover_migrations(Path(__file__).parent)
            # Disconnect once within each migration and once after its final DDL.
            for migration in migrations:
                for statement in (migration.statements[len(migration.statements) // 2], migration.statements[-1]):
                    with self.assertRaises(InjectedDisconnect):
                        apply_migrations(InterruptedConnection(db, statement), [migration])
                    db = connect()
                    with db.cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM schema_migration WHERE migration_name=%s", (migration.name,))
                        self.assertEqual(cur.fetchone()[0], 0)
                self.assertEqual(apply_migrations(db, [migration]), [migration.name])
                self.assertEqual(apply_migrations(db, [migration]), [])
            with db.cursor() as cur:
                cur.execute("SELECT payload FROM t2_preserved_input WHERE id=1")
                self.assertIn("20261109", cur.fetchone()[0])
            before = inventory(db)
            self.assertEqual(apply_migrations(db, migrations), [])
            self.assertEqual(compare(before, inventory(db))["status"], "pass")
        finally:
            db.close()

        store = PublicationStore(MarketCalcRepository(config))
        fact = {"sourceId": "t2-ordinary", "symbol": "002724", "eventDirection": "up"}
        snapshot = {"snapshotId": "1", "items": [], "unresolvedFacts": []}
        store.publish(snapshot, [fact], "t2-1")
        with self.assertRaises(PublicationUnavailable):
            store.publish(dict(snapshot, items=[{"symbol": "bad"}]),
                          [{"sourceId": "t2-bad", "symbol": "bad"}], "t2-2")
        self.assertEqual(store.active_snapshot(), snapshot)
        self.assertEqual(len(store.rebuild_facts()), 1)
        self.assertEqual(store.health()["outboxPending"], 1)


if __name__ == "__main__":
    unittest.main()
