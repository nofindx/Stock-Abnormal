import json
from pathlib import Path
import unittest

from .evidence_gate import evidence_gate


class EvidenceGateTests(unittest.TestCase):
    def test_checked_in_manifest_remains_blocked_until_official_review(self):
        path = Path(__file__).parents[1] / "fixtures" / "vnext" / "rule_evidence_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        result = evidence_gate(manifest["entries"])
        self.assertEqual(result["status"], "block")
        self.assertTrue(result["missingOrUnapproved"])

    def test_approved_entry_requires_url_and_effective_date(self):
        self.assertEqual(evidence_gate([{
            "rule_key": "x", "review_status": "approved", "source_url": "https://example.test/rule", "effective_from": "20260706"
        }])["status"], "pass")


if __name__ == "__main__":
    unittest.main()
