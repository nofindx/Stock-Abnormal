#!/usr/bin/env python3
"""Verify persisted 10-day same-direction counts against return vectors.

The check reads the calculation-bases API only.  It does not request daily
bars, realtime quotes, or write any data.  The calculation is deliberately
the same shared ``MarketService._vector_metrics`` implementation used by the
detail endpoint, so a mismatch points to stale or incorrectly persisted data
rather than a second business algorithm.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

# Allow direct execution from the repository root, matching the other tool
# scripts documented for acceptance checks.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.core.abnormal_rules import BOARD_RULES, Board
from server.services.market_service import MarketService


BOARD_BY_LABEL = {
    "主板": Board.MAIN,
    "创业板": Board.CHINEXT,
    "科创板": Board.STAR,
    "北交所": Board.BSE,
}


def get_json(url: str) -> dict:
    with urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit 必须为 1～100")

    base = args.base_url.rstrip("/")
    offset = 0
    total = None
    items = []
    indexes = {}
    while total is None or offset < total:
        payload = get_json(
            f"{base}/api/market/calculation-bases?offset={offset}&limit={args.limit}"
        )
        if payload.get("code") != 0:
            raise RuntimeError(payload)
        data = payload.get("data") or {}
        chunk = data.get("items") or []
        items.extend(chunk)
        for index in data.get("indexes") or []:
            indexes[str(index.get("indexCode") or "")] = index
        pagination = data.get("pagination") or {}
        total = int(pagination.get("total") or len(items))
        if not chunk:
            break
        offset += len(chunk)

    mismatches = []
    missing_indexes = []
    for item in items:
        index_code = str(item.get("indexCode") or "")
        index = indexes.get(index_code)
        if not index:
            missing_indexes.append(str(item.get("ts_code") or ""))
            continue
        board = BOARD_BY_LABEL.get(str(item.get("board") or ""), Board.MAIN)
        metrics = MarketService._vector_metrics(
            item.get("returnVector") or [],
            index.get("returnVector") or [],
            BOARD_RULES[board],
        )
        stored = item.get("sameDirection") or {}
        expected = {"up": int(metrics.get("up") or 0), "down": int(metrics.get("down") or 0)}
        actual = {"up": int(stored.get("up") or 0), "down": int(stored.get("down") or 0)}
        if actual != expected:
            mismatches.append({"ts_code": item.get("ts_code"), "actual": actual, "expected": expected})

    print(json.dumps({
        "checked": len(items),
        "total": total,
        "indexCount": len(indexes),
        "missingIndex": len(missing_indexes),
        "mismatch": len(mismatches),
        "sampleMismatches": mismatches[:20],
    }, ensure_ascii=False, indent=2))
    return 1 if missing_indexes or mismatches else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, HTTPError, URLError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"same-direction check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
