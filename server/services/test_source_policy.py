import os
import json
import unittest
from unittest.mock import patch

from .source_policy import load_source_policy, load_source_policies, policy_hash, source_policy_gate


class SourcePolicyTests(unittest.TestCase):
    def test_missing_hash_blocks_g26(self):
        with patch.dict(os.environ, {"SOURCE_POLICY_HASH": "abc123"}, clear=True):
            with self.assertRaises(ValueError):
                load_source_policy()

    def test_frozen_policy_passes_g26(self):
        policy = {
            "version": "test-policy-v1",
            "market_phase": "continuous",
            "freshness_seconds": 5,
            "stale_seconds": 30,
            "max_quote_skew_seconds": 5,
        }
        content_hash = policy_hash(policy)
        payload = {**policy, "content_hash": content_hash}
        with patch.dict(os.environ, {
            "SOURCE_POLICY_JSON": json.dumps(payload),
            "SOURCE_POLICY_HASH": content_hash,
        }, clear=True):
            policy = load_source_policy()
        result = source_policy_gate([policy])
        self.assertEqual(result["status"], "pass")

    def test_mismatched_config_hash_blocks_g26(self):
        policy = {
            "version": "test-policy-v1",
            "market_phase": "continuous",
            "freshness_seconds": 5,
            "stale_seconds": 30,
            "max_quote_skew_seconds": 5,
        }
        content_hash = policy_hash(policy)
        payload = {**policy, "content_hash": content_hash}
        with patch.dict(os.environ, {
            "SOURCE_POLICY_JSON": json.dumps(payload),
            "SOURCE_POLICY_HASH": "different",
        }, clear=True):
            with self.assertRaises(ValueError):
                load_source_policy()

    def test_source_policy_array_requires_and_loads_each_source(self):
        entries = []
        for name in ("tencent", "sina"):
            item = {
                "version": "test-policy-v1", "source_name": name, "market_phase": "continuous",
                "freshness_seconds": 5, "stale_seconds": 30, "max_quote_skew_seconds": 5,
            }
            entries.append({**item, "content_hash": policy_hash(item)})
        aggregate = policy_hash({"policies": entries})
        with patch.dict(os.environ, {
            "SOURCE_POLICY_JSON": json.dumps(entries), "SOURCE_POLICY_HASH": aggregate,
        }, clear=True):
            policies = load_source_policies()
        self.assertEqual([item.source_name for item in policies], ["tencent", "sina"])


if __name__ == "__main__":
    unittest.main()
