import hashlib
from pathlib import Path
import tempfile
import unittest

from server.migrations.runner import Migration, apply_migrations, discover_migrations, parse_migration


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.db.executed.append((normalized, params))
        if normalized.startswith("SELECT checksum"):
            self.result = (self.db.applied[params[0]],) if params[0] in self.db.applied else None
        elif normalized.startswith("INSERT INTO schema_migration"):
            self.db.applied[params[0]] = params[1]

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self):
        self.applied = {}
        self.executed = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1


class MigrationRunnerTests(unittest.TestCase):
    def test_applies_pending_migrations_and_is_idempotent(self):
        migration = Migration("001_first.sql", "a" * 64, ("CREATE TABLE x (id INT)",))
        connection = FakeConnection()
        self.assertEqual(apply_migrations(connection, [migration]), ["001_first.sql"])
        self.assertEqual(apply_migrations(connection, [migration]), [])
        sql = [entry[0] for entry in connection.executed]
        self.assertEqual(sum(statement == "CREATE TABLE x (id INT)" for statement in sql), 1)

    def test_rejects_changed_checksum_for_applied_migration(self):
        connection = FakeConnection()
        connection.applied["001_first.sql"] = "a" * 64
        changed = Migration("001_first.sql", "b" * 64, ("CREATE TABLE x (id INT)",))
        with self.assertRaisesRegex(ValueError, "内容被修改"):
            apply_migrations(connection, [changed])

    def test_parse_migration_hashes_raw_file_and_splits_statements(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "001_sample.sql"
            raw = b"-- note\nCREATE TABLE a (id INT);\nCREATE TABLE b (id INT);\n"
            path.write_bytes(raw)
            migration = parse_migration(path)
        self.assertEqual(migration.checksum, hashlib.sha256(raw).hexdigest())
        self.assertEqual(migration.statements, ("CREATE TABLE a (id INT)", "CREATE TABLE b (id INT)"))

    def test_rejects_non_versioned_migration_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "init.sql"
            path.write_text("CREATE TABLE x (id INT);", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "非法迁移文件名"):
                parse_migration(path)

    def test_repository_migrations_are_utf8_and_nonempty(self):
        migrations = discover_migrations(Path(__file__).parent)
        self.assertEqual([item.name for item in migrations], ["001_vnext_foundation.sql", "002_vnext_core.sql"])
        self.assertTrue(all(item.statements for item in migrations))


if __name__ == "__main__":
    unittest.main()
