import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from server.release import prepare


class ReleaseTests(unittest.TestCase):
    def test_frontend_backend_release_identifiers_match(self):
        root = Path(__file__).resolve().parents[1]
        backend = json.loads((root / "server/release_info.json").read_text())
        frontend = json.loads((root / "miniprogram/release-info.json").read_text())
        self.assertEqual(backend, frontend)
        self.assertRegex(backend["miniProgramVersion"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(backend["quoteRefreshPolicy"], "latest-available-all-hours")

    def test_release_applies_only_additive_003_before_api(self):
        repository = MagicMock()
        repository.available = True
        repository.config = {"database": "stock_abnormal"}
        db = repository.connection.return_value.__enter__.return_value
        db.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
        with patch("server.release.apply_migrations", return_value=["003_shared_publications.sql"]) as apply:
            prepare(repository)
        migrations = apply.call_args[0][1]
        self.assertEqual([m.name for m in migrations], ["003_shared_publications.sql"])
        self.assertTrue(all(s.startswith("CREATE TABLE IF NOT EXISTS publication_") for s in migrations[0].statements))

    def test_release_rejects_wrong_database(self):
        with self.assertRaises(RuntimeError):
            prepare(SimpleNamespace(available=True, config={"database": "different"}))
