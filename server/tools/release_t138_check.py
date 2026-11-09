"""T1/T3/T8 evidence capture. No settings, facts or market datasets are changed.

Cloud S1/S2 operations create the same short-lived read views as a normal user.
Never serialize tokens, signed URLs, raw responses or secrets into the report.
Exit 1 means a checked policy/contract failed; 2 means execution was incomplete.
"""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.services.market_service import MarketService, SHANGHAI_TZ
from server.rules.evidence_gate import evidence_gate
from server.rules.catalog import BOARD_RULES, Board


def fixtures():
    root = Path(__file__).resolve().parents[2]
    paths = ["server/fixtures/vnext/rule_evidence_manifest.json",
             "server/fixtures/vnext/product_policy_cases.json", "server/fixtures/vnext/replay_same_direction.json",
             "server/fixtures/production_regression_20261101.json", "server/fixtures/aoya_prediction_boundary_20261103.json",
             "server/fixtures/huayang_same_direction_next_day_20261104.json"]
    manifest = json.loads((root / paths[0]).read_text())
    return {"evidenceMetadataGate": evidence_gate(manifest["entries"]),
            "authority": manifest["authority"], "policyVersion": manifest["policy_version"],
            "files": [{"path": path, "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()} for path in paths],
            "limits": "Metadata/hash pass does not certify all fixture semantics or production paths."}


def local_policy():
    service = MarketService()
    try:
        service.client = SimpleNamespace(
            trade_cal=lambda *_: SimpleNamespace(to_dict=lambda *_: [
                {"cal_date": day, "is_open": 1} for day in ("20261009", "20261012", "20261013")]),
            latest_trade_date=lambda: "20261009")
        service.set_clock(lambda: datetime(2026, 10, 12, 12, tzinfo=SHANGHAI_TZ))
        lunch = service._detail_realtime_allowed()
        service.set_clock(lambda: datetime(2026, 10, 11, 12, tzinfo=SHANGHAI_TZ))
        state = service._prediction_state()
        dates = ["20260921", "20260922", "20260923", "20260924", "20260925"]
        stock = [{"date": day, "return": val} for day, val in zip((dates[0], dates[1], dates[4]), (80, 10, 5))]
        index = [{"date": day, "return": 0} for day in dates]
        trimmed, aligned_index, meta = service._trim_vectors_after_suspension(stock, index)
        resumed_metrics = service._vector_metrics(
            trimmed, aligned_index, BOARD_RULES[Board.MAIN],
            price_reset_dates=[meta["resetDate"]] if meta["resetDate"] else [],
            ordinary_events=[{"sourceId": "pre-resume-ordinary", "endDate": "20260922", "direction": "up"}],
        )
        # A split-adjusted reference is reflected in the vendor's daily
        # pct_chg. Adjacent unadjusted closing prices are not interchangeable.
        calculations = {"600000.SH": {"latest_close": 10.0, "as_of_trade_date": "20261009",
                                        "stock_return_vector": []}}
        frame = SimpleNamespace(empty=False, to_dict=lambda *_: [{"ts_code": "600000.SH",
            "trade_date": "20261012", "close": 5.1, "pre_close": 5.0, "pct_chg": 2.0}])
        service._append_daily_rows(calculations, {"20261012": frame}, ["20261012"])
        appended_return = calculations["600000.SH"]["stock_return_vector"][-1]["return"]
        return [
            {"id": "T1-ADJUSTED-REFERENCE-INCREMENT", "status": "pass" if abs(appended_return - 2) < 1e-8 else "fail",
             "expected": {"dailyReturn": 2.0, "reference": "5.1 / 5.0 - 1"},
             "actual": {"dailyReturn": appended_return, "reference": "source pct_chg, falling back to same-row pre_close"}},
            {"id": "T1-RESUME-BASELINE", "status": "pass" if all(resumed_metrics["deviations"][window] == 0 for window in (3, 10, 30)) and resumed_metrics["up"] == 1 else "fail",
             "expected": {"deviationsOnResumeDate": {"3": 0, "10": 0, "30": 0}, "ordinaryEventCountContinues": 1},
             "actual": {"deviations": resumed_metrics["deviations"], "upCount": resumed_metrics["up"], "resumeDate": meta["resetDate"]}},
            {"id": "T8-LUNCH-S2", "status": "pass" if lunch else "fail",
             "expected": True, "actual": lunch},
            {"id": "T8-REST-DATE-ROLE", "status": "pass" if state["tradeDate"] == "20261009" else "fail",
             "expected": {"D": "20261009", "N": "20261012"},
             "actual": {"tradeDate": state["tradeDate"], "targetTradeDate": state["targetTradeDate"],
                        "previousTradeDate": state["previousTradeDate"]}},
        ]
    finally:
        service.close()


def cloud_checks(base):
    def request(path, values=None, method="GET"):
        body = None
        if values is not None:
            if method == "GET":
                path += "?" + urlencode(values)
            else:
                body = json.dumps(values).encode()
        req = Request(base.rstrip("/") + path, data=body, method=method,
                      headers={"Content-Type": "application/json"})
        try:
            response = urlopen(req, timeout=30)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def get(path, values=None):
        status, response = request(path, values)
        assert status == 200 and response.get("code") == 0, "successful read required"
        return response["data"]

    health = get("/health")
    health_summary = {
        "service": health.get("service"), "publications": {
            key: health.get("vnext", {}).get("publications", {}).get(key)
            for key in ("backend", "available", "factCount", "outboxPending")},
        "monitor": {key: health.get("monitor", {}).get(key)
                    for key in ("available", "snapshotDate", "lastStatus", "itemCount")},
        "prediction": {scope: {key: health.get("prediction", {}).get(scope, {}).get(key)
                               for key in ("status", "ruleVersion", "targetTradeDate", "available")}
                       for scope in ("today", "next_day")},
    }
    scope = {"page": "prediction", "role": "current", "entry_id": "t138-" + uuid.uuid4().hex}
    context = get("/api/v2/context", scope)
    assert context == get("/api/v2/context", scope), "idempotent context differs"
    token = context["read_token"]["value"]
    query = {"read_token": token, "role": "current"}
    first = get("/api/v2/predictions", query)
    assert first["dataset_id"] == context["dataset_id"], "S1 dataset differs"
    assert request("/api/v2/predictions", dict(query, role="next"))[0] == 403, "cross-role read allowed"
    assert request("/api/v2/stocks/detail", {"read_token": token, "security_id": "600825.SH"})[0] == 403, "cross-page read allowed"
    assert request("/api/v2/predictions", dict(query, read_token="invalid"))[0] == 409, "invalid token not rejected"
    assert request("/api/v2/context", dict(scope, role="next"))[0] == 409, "entry scope conflict accepted"

    def read(_):
        started = time.monotonic()
        assert get("/api/v2/predictions", query) == first, "pinned S1 response changed"
        return (time.monotonic() - started) * 1000

    with ThreadPoolExecutor(max_workers=4) as pool:
        latencies = sorted(pool.map(read, range(16)))
    stock_context = get("/api/v2/context", {"page": "stock", "role": "current",
        "security_id": "600825.SH", "entry_id": "t138-" + uuid.uuid4().hex})
    assert request("/api/v2/stocks/detail", {"read_token": stock_context["read_token"]["value"],
        "security_id": "002724.SZ"})[0] == 403, "cross-security read allowed"
    assert request("/api/v2/realtime/refresh", {"read_token": stock_context["read_token"]["value"],
        "page": "stock", "security_id": "002724.SZ"}, "POST")[0] == 403, "cross-security refresh allowed"

    # This is a single normal-user refresh of the pinned pool, not market scan.
    status, response = request("/api/v2/realtime/refresh", {"read_token": token}, "POST")
    for _ in range(8):
        if status != 202:
            break
        time.sleep(0.5)
        status, response = request("/api/v2/jobs/" + response["data"]["job_id"], {"read_token": token})
    assert status == 200 and response.get("code") == 0, "S2 did not reach terminal response"
    s2 = response["data"]
    for key in ("dataset_id", "pool_hash", "rule_version", "time_context_id"):
        assert s2[key] == context[key], "S2 changed authorized basis"
    assert get("/api/v2/predictions", query) == first, "S2 mutated original S1"

    monitor = get("/api/monitor")
    fantuo = [{key: item.get(key) for key in ("symbol", "riskTone", "monitorStartDate", "monitorEndDate", "days")}
              for item in monitor.get("items", []) if item.get("symbol") == "301313"]
    return {"status": "pass", "health": health_summary,
            "context": {key: context.get(key) for key in ("dataset_id", "dataset_version", "pool_hash",
                "pool_count", "rule_version", "time_context_id", "target_date", "expires_at")},
            "phase": first.get("phase"), "tradeDate": first.get("tradeDate"),
            "targetTradeDate": first.get("targetTradeDate"), "cards": len(first.get("items", [])),
            "authorization": "idempotent/cross-role/cross-page/cross-stock/invalid-token pass",
            "concurrentReads": {"requests": 16, "concurrency": 4, "identical": True,
                "sampleP95Ms": round(latencies[14], 1), "maxMs": round(latencies[-1], 1)},
            "s2": {key: s2.get(key) for key in ("refresh_state", "result_state", "refreshStatus", "reasons")},
            "fantuoFacts": fantuo,
            "monitorTimeContextPresent": bool(monitor.get("time_context_id")),
            "limits": ["no instance identity; does not prove two cloud instances",
                       "point-in-time sample; does not prove a full live cross-day cycle"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = {"businessDate": args.business_date, "observedAt": datetime.now(SHANGHAI_TZ).isoformat(),
              "fixtures": fixtures(), "localPolicyChecks": local_policy()}
    if args.base_url:
        try:
            report["cloud"] = cloud_checks(args.base_url)
        except Exception as exc:
            # Never stringify network errors: their URL may contain a token.
            report["cloud"] = {"status": "incomplete", "errorType": type(exc).__name__,
                               "assertion": str(exc) if isinstance(exc, AssertionError) else ""}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("cloud", {}).get("status") == "incomplete":
        return 2
    return 1 if any(item["status"] == "fail" for item in report["localPolicyChecks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
