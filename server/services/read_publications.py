"""Capture S1 inputs and serve the exact authorized content in S1/S2."""
from copy import copy, deepcopy
from datetime import timedelta
import hashlib
from types import SimpleNamespace
from server.data.publication_store import canonical, PublicationUnavailable
from server.services.market_service import PREDICTION_RULE_VERSION, _index_code_for_stock
from server.services.context_service import ContextService


class ReadPublications:
    def __init__(self, service, store):
        self.service, self.store = service, store

    def capture(self, scope):
        # Pin announcement facts once per request; never let each stock read a
        # different worker's latest snapshot during the same calculation.
        snapshot = deepcopy(self.service._monitor_provider() or {}) if self.service._monitor_provider else {}
        service = copy(self.service)
        service._monitor_provider = lambda: snapshot
        page, role = scope["page"], scope["role"]
        if page == "prediction":
            data = service.predictions("today" if role == "current" else "next_day")
            codes = sorted({x.get("ts_code") for x in data.get("items", []) if x.get("ts_code")})
        elif page == "stock":
            codes = [scope["security_id"]]
            data = None
        else:
            data = deepcopy(snapshot)
            codes = []
        rows = {x["ts_code"]: x for x in self.service._stock_rows() if x["ts_code"] in codes}
        repo = self.service._calc_repository
        calculations = {code: repo.get_calc(code) for code in codes}
        index_codes = {_index_code_for_stock(row) for row in rows.values()}
        indexes = {code: repo.get_index(code) for code in index_codes}
        if page == "stock":
            # Render S1 from the exact inputs retained for S2, rather than
            # reading the stock/index again after producing the visible body.
            service._calc_repository = SimpleNamespace(available=True,
                get_calc=lambda code: deepcopy(calculations.get(code)),
                get_index=lambda code: deepcopy(indexes.get(code)))
            data = service.detail(scope["security_id"], use_realtime=False)
            data["realtimeRefreshAllowed"] = service._detail_realtime_allowed()
        symbols = {x.split(".")[0] for x in codes}
        for key in ("items", "resetFacts", "unresolvedFacts"):
            snapshot[key] = [x for x in snapshot.get(key, []) if str(x.get("symbol") or "").split(".")[0] in symbols]
        if page == "stock":
            data["monitoringFacts"] = deepcopy(snapshot["items"])
        basis = {"stocks": rows, "calculations": calculations, "indexes": indexes, "monitor": snapshot}
        payload = {"scope": scope, "data": data, "basis": basis}
        identity = self.store.freeze_read(payload, scope)
        members = sorted((str(x.get("ts_code") or ""), str(x.get("ruleKey") or "")) for x in data.get("items", [])) if page == "prediction" else codes
        pool_hash = hashlib.sha256(canonical(members).encode()).hexdigest()
        return identity, payload, pool_hash, len(codes)

    def get(self, context):
        payload = self.store.get(context.realtime_view_id or context.dataset_id, "read")
        # Dataset labels alone are insufficient: bind the actual saved scope.
        if payload["scope"].get("role") != context.role:
            raise PublicationUnavailable("read scope mismatch")
        if getattr(context, "query_hash", None) and context.query_hash != ContextService.query_hash(payload["scope"]):
            raise PublicationUnavailable("read authorization scope mismatch")
        return payload

    def refresh(self, context):
        payload = self.get(context)
        scope, basis = payload["scope"], payload["basis"]
        service = copy(self.service)
        service._monitor_provider = lambda: deepcopy(basis["monitor"])
        service._stock_rows = lambda: list(deepcopy(basis["stocks"]).values())
        saved = deepcopy(payload["data"])
        service._prediction_cache = {}
        records = {"items": saved.get("items") or [], "target_trade_date": saved.get("targetTradeDate") or context.target_date}
        service._calc_repository = SimpleNamespace(
            available=True,
            get_calc=lambda code: deepcopy(basis["calculations"].get(code)),
            get_index=lambda code: deepcopy(basis["indexes"].get(code)),
            all_calculations=lambda: deepcopy(basis["calculations"]),
            all_indexes=lambda: deepcopy(basis["indexes"]),
            get_prediction=lambda *_args, **_kwargs: deepcopy(records),
        )
        if scope["page"] == "prediction":
            forecast_scope = "today" if scope["role"] == "current" else "next_day"
            items = service._refresh_existing_prediction_scope(forecast_scope, forecast_scope)
            allowed = {(x.get("ts_code"), x.get("ruleKey")) for x in records["items"]}
            if any((x.get("ts_code"), x.get("ruleKey")) not in allowed for x in items):
                raise PublicationUnavailable("realtime refresh exceeded authorized pool")
            # Failure must retain S1 contents, never publish an empty success.
            by_key = {(x.get("ts_code"), x.get("ruleKey")): x for x in items if x.get("quoteAvailable")}
            saved["items"] = [by_key.get((x.get("ts_code"), x.get("ruleKey")), x) for x in records["items"]]
            prices_available = not records["items"] or len(items) == len(records["items"]) and all(x.get("quoteAvailable") for x in items)
            fresh = prices_available and all(x.get("calculationQuoteAvailable") for x in items)
            saved.update(refresh_state="ready", result_state="fresh" if fresh else "stale",
                         refreshStatus="applied" if prices_available else "fallback",
                         reasons=[] if fresh else ["INDEX_QUOTE_UNAVAILABLE" if prices_available else "REALTIME_SOURCE_UNAVAILABLE"])
            saved["dataQuality"] = dict(saved.get("dataQuality") or {},
                quoteAvailable=prices_available, source="腾讯/新浪/东方财富" if prices_available else (saved.get("dataQuality") or {}).get("source", ""))
            times = [str(x.get("quoteUpdatedAt")) for x in items if x.get("quoteAvailable") and x.get("quoteUpdatedAt")]
            if times:
                saved["quoteUpdatedAt"] = max(times)
            saved["realtimeRefreshAllowed"] = True
        elif scope["page"] == "stock":
            code = scope["security_id"]
            previous = saved
            saved = service._detail_from_repository(basis["stocks"][code], basis["calculations"][code])
            if saved is None:
                raise PublicationUnavailable("pinned calculation basis unavailable")
            if not (saved.get("dataQuality") or {}).get("quoteAvailable") and (previous.get("dataQuality") or {}).get("quoteUpdatedAt"):
                # A later failed manual refresh retains this authorized view's
                # last price, rather than regressing to its older S1 close.
                saved = previous
                saved["dataQuality"] = dict(saved.get("dataQuality") or {},
                    quoteAvailable=False, realtime=False, fallback=True, message="最新行情暂不可用，保留上次数据")
            saved["monitoringFacts"] = deepcopy(basis["monitor"].get("items") or [])
            saved["realtimeRefreshAllowed"] = True
            price_available = bool((saved.get("dataQuality") or {}).get("quoteAvailable"))
            fresh = price_available and bool((saved.get("dataQuality") or {}).get("realtime"))
            saved.update(refresh_state="ready", result_state="fresh" if fresh else "stale",
                         refreshStatus="applied" if price_available else "fallback",
                         reasons=[] if fresh else ["INDEX_QUOTE_UNAVAILABLE" if price_available else "REALTIME_SOURCE_UNAVAILABLE"])
        else:
            raise PublicationUnavailable("scope does not support realtime refresh")
        view = self.store.freeze_read({"scope": scope, "data": saved, "basis": basis}, scope)
        return saved, view

    @staticmethod
    def next_boundary(now):
        candidates = [now + timedelta(seconds=300)]
        for hour, minute in ((0, 0), (9, 0), (9, 15), (15, 0)):
            value = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if value <= now:
                value += timedelta(days=1)
            candidates.append(value)
        return min(candidates).timestamp()
