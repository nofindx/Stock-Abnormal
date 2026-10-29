"""Release gate for versioned product-policy rule manifests.

The product has deliberately chosen ``product_policy`` as the executable
authority for this release.  The manifest remains versioned and replayable,
but it does not pretend that every product convention is an exchange-issued
text.  External URLs are optional evidence only.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable


def evidence_gate(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(entries)
    required = ("rule_key", "scope", "effective_from", "policy_basis", "fixture_refs")
    missing = []
    for row in rows:
        key = row.get("rule_key", "")
        reasons = []
        if row.get("source_type") != "product_policy":
            reasons.append("source_type")
        if row.get("review_status") != "approved_by_product":
            reasons.append("review_status")
        reasons.extend(field for field in required if not row.get(field))
        digest = hashlib.sha256(str(row.get("policy_basis") or "").encode("utf-8")).hexdigest()
        if not re.fullmatch(r"\d{8}", str(row.get("effective_from") or "")):
            reasons.append("effective_from_format")
        if row.get("content_hash") != digest:
            reasons.append("content_hash")
        if reasons:
            missing.append({"rule_key": key, "reasons": reasons})
    return {
        "gateId": "G21",
        "status": "pass" if rows and not missing else "block",
        "requiredCount": len(rows),
        "missingOrUnapproved": missing,
    }
