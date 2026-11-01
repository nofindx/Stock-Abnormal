"""Read-only production regression checks; never print signed read/PDF URLs.

S1/context and off-hours S2 requests create only normal short-lived read records.
No market-source data, settings, or announcement facts are changed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expected-as-of", default="20261030")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    def request(path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request(base + path, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=30) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def get(path):
        status, payload = request(path)
        assert status == 200 and payload.get("code") == 0, ("GET failed", path.split("?")[0], status)
        return payload["data"]

    health = get("/health")
    assert health["vnext"]["publications"]["backend"] == "mysql", "shared MySQL unavailable"
    assert health["monitor"]["available"], "announcement publication not ready"
    today = get("/api/predictions?scope=today")
    assert today["targetTradeDate"] == args.expected_as_of, "as-of differs; update expected fixture date"
    keys = {(item["ts_code"], item["ruleKey"]): item for item in today["items"]}
    for code in ("688836.SH", "301502.SZ", "688585.SH", "688286.SH"):
        assert (code, "severe_10d") in keys, ("missing today candidate", code)
    assert ("301313.SZ", "ordinary_3d") in keys, "missing Fantuo ordinary candidate"
    for code in ("002724.SZ", "603278.SH"):
        assert keys[(code, "severe_10d")]["triggered"], ("triggered card lost", code)

    monitor = get("/api/monitor")
    ocean = [item for item in monitor["items"] if item.get("symbol") == "002724"]
    tones = {item.get("riskTone") for item in ocean}
    assert "ordinary" in tones and any(str(tone).startswith("severe") for tone in tones), "Ocean King needs independent ordinary and severe cards"
    ocean_detail = get("/api/stocks/detail?ts_code=002724.SZ")
    direction = next(item for item in ocean_detail["warnings"] if item["title"] == "10 日同向")
    assert str(direction.get("up")) == "3", ("Ocean King formal same-direction count", direction.get("up"))
    xinhua = get("/api/stocks/detail?ts_code=600825.SH")
    assert xinhua["stockState"]["status"] != "unknown", "Xinhua still unknown"
    assert all((item.get("up") is not None and item.get("down") is not None)
               if item["title"] == "10 日同向" else item.get("value") not in (None, "--", "")
               for item in xinhua["warnings"]), "Xinhua warnings unavailable"
    print(json.dumps({"business": "pass", "asOf": args.expected_as_of,
                      "todayCards": len(today["items"]), "oceanSameDirection": direction["up"],
                      "oceanMonitorTones": sorted(tones), "xinhuaState": xinhua["stockState"]["status"]}, ensure_ascii=False))

    entry = "acceptance-" + uuid.uuid4().hex
    query = {"page": "prediction", "role": "current", "entry_id": entry}
    context = get("/api/v2/context?" + urlencode(query))
    repeated = get("/api/v2/context?" + urlencode(query))
    assert context == repeated, "same entry must return same context"
    assert len(context["pool_hash"]) == 64 and context["pool_hash"] != "legacy", "pool not pinned"
    token = context["read_token"]["value"]
    path = "/api/v2/predictions?" + urlencode({"read_token": token, "role": "current"})
    first = get(path)
    status, _ = request("/api/v2/predictions?" + urlencode({"read_token": token, "role": "next"}))
    assert status == 403, "cross-role read allowed"
    status, _ = request("/api/v2/predictions?" + urlencode({"read_token": "invalid"}))
    assert status == 409, "invalid context must require reacquisition"
    latencies = []

    def fixed_read(_):
        start = time.monotonic()
        saved = get(path)
        assert saved == first, "authorized S1 body changed"
        return (time.monotonic() - start) * 1000

    with ThreadPoolExecutor(max_workers=4) as pool:
        latencies = list(pool.map(fixed_read, range(16)))
    print(json.dumps({"readContract": "pass", "sampleRequests": len(latencies), "concurrency": 4,
                      "sampleP95Ms": round(sorted(latencies)[-2], 1), "sampleMaxMs": round(max(latencies), 1)}))

    for tone in ("ordinary", "severe", "delisting"):
        candidates = [item for item in monitor["items"] if item.get("pdfAvailable") and item.get("sourceUrl") and
                      (str(item.get("riskTone", "")).startswith(tone))]
        if not candidates:
            print(json.dumps({"pdfClass": tone, "status": "no-active-sample"}))
            continue
        req = Request(candidates[0]["sourceUrl"])
        with urlopen(req, timeout=45) as response:
            data = response.read()
            assert response.headers.get("Content-Type", "").startswith("application/pdf"), "PDF content type"
        assert data.startswith(b"%PDF"), "PDF binary missing"
        from pypdf import PdfReader
        pages = len(PdfReader(BytesIO(data)).pages)
        assert pages > 0, "PDF has no pages"
        print(json.dumps({"pdfClass": tone, "status": "pass", "bytes": len(data), "pages": pages}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # HTTP exception representations may contain signed query strings.
        message = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        print("Production acceptance failed: " + message, file=sys.stderr)
        raise SystemExit(1)
