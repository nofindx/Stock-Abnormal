"""Release gate for versioned rule evidence manifests."""

from __future__ import annotations

from typing import Any, Dict, Iterable


def evidence_gate(entries: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(entries)
    missing = [row.get("rule_key", "") for row in rows if row.get("review_status") != "approved" or not row.get("source_url") or not row.get("effective_from")]
    return {
        "gateId": "G21",
        "status": "pass" if rows and not missing else "block",
        "requiredCount": len(rows),
        "missingOrUnapproved": missing,
    }
