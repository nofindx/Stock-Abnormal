import json
import hashlib
from pathlib import Path
import unittest

from .evidence_gate import evidence_gate


class EvidenceGateTests(unittest.TestCase):
    def test_checked_in_product_policy_manifest_passes(self):
        path = Path(__file__).parents[1] / "fixtures" / "vnext" / "rule_evidence_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        result = evidence_gate(manifest["entries"])
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["missingOrUnapproved"])

    def test_product_policy_entry_requires_replay_metadata(self):
        basis = "产品规则 v1"
        self.assertEqual(evidence_gate([{
            "rule_key": "x", "scope": "主板", "source_type": "product_policy",
            "review_status": "approved_by_product", "effective_from": "20261029",
            "policy_basis": basis, "fixture_refs": ["F-X"],
            "content_hash": hashlib.sha256(basis.encode("utf-8")).hexdigest()
        }])["status"], "pass")

    def test_external_url_is_optional_for_product_policy(self):
        basis = "产品规则 v1"
        result = evidence_gate([{
            "rule_key": "x", "scope": "主板", "source_type": "product_policy",
            "review_status": "approved_by_product", "effective_from": "20261029",
            "policy_basis": basis, "fixture_refs": ["F-X"],
            "content_hash": hashlib.sha256(basis.encode("utf-8")).hexdigest(),
            "source_url": ""
        }])
        self.assertEqual(result["status"], "pass")

    def test_manifest_content_hash_must_match_policy_text(self):
        basis = "产品规则 v1"
        result = evidence_gate([{
            "rule_key": "x", "scope": "主板", "source_type": "product_policy",
            "review_status": "approved_by_product", "effective_from": "20261029",
            "policy_basis": basis, "fixture_refs": ["F-X"], "content_hash": "0" * 64
        }])
        self.assertEqual(result["status"], "block")

    def test_manifest_fixture_references_are_present(self):
        root = Path(__file__).parents[1] / "fixtures" / "vnext"
        manifest = json.loads((root / "rule_evidence_manifest.json").read_text(encoding="utf-8"))
        fixture = json.loads((root / "product_policy_cases.json").read_text(encoding="utf-8"))
        ids = {item["id"] for item in fixture["cases"]}
        self.assertTrue(all(ref in ids for row in manifest["entries"] for ref in row["fixture_refs"]))


if __name__ == "__main__":
    unittest.main()
