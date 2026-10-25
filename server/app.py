"""异动查查 HTTP API 入口。

运行：
    TUSHARE_TOKEN=... python3 -m server.app

接口统一返回 {code, data, message}，便于小程序请求层处理加载、失败和降级状态。
"""

from __future__ import annotations

import json
import os
import re
from html import escape
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .services.market_service import CalculationDataUnavailable, MarketService, SHANGHAI_TZ
from .services.announcement_service import AnnouncementService
from .services.monitor_service import OfficialMonitorService
from .services.tushare_client import TushareUnavailable


SERVICE = MarketService()
ANNOUNCEMENTS = AnnouncementService()
# 重点监控是独立的公告快照链路，不再从全市场预测结果推导。
OFFICIAL_MONITOR = OfficialMonitorService(SERVICE.client, ANNOUNCEMENTS)
SERVICE.set_monitor_provider(OFFICIAL_MONITOR.repository.active_snapshot)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _html_bytes(value: str) -> bytes:
    return value.encode("utf-8")


def _health_ui(payload: dict) -> str:
    """Render a small human-readable health page without changing the JSON contract."""

    data = payload.get("data") or {}
    market = data.get("marketCalc") or {}
    sources = data.get("dataSources") or {}
    prediction = data.get("prediction") or {}
    monitor = data.get("monitor") or {}
    coverage = market.get("stockBasicCoverage") or {}
    migration = market.get("predictionMigration") or {}

    issues = []
    if not data.get("tushareConfigured"):
        issues.append("Tushare 未配置")
    if market.get("lastErrorActive"):
        issues.append(market.get("lastError") or "基础行情任务失败")
    if coverage and coverage.get("complete") is False:
        issues.append("股票基础资料不完整")
    for scope, label in (("today", "当日"), ("next_day", "次日")):
        state = prediction.get(scope) or {}
        if state.get("status") in {"failed", "missing", "incomplete", "rule_version_mismatch"} or state.get("stale"):
            issues.append(f"{label}预测数据不可用或已过期")
        if state.get("lastError"):
            issues.append(f"{label}预测最近一次生成失败")
        if state.get("datasetQuoteComplete") is False and state.get("available"):
            issues.append(f"{label}行情数据未完整")
    overall = "异常" if issues else "正常"
    overall_class = "bad" if issues else "ok"

    def text(value, fallback="-"):
        return escape(str(value if value not in (None, "") else fallback))

    def status(value):
        return "正常" if value else "不可用"

    def count(scope):
        return text((prediction.get(scope) or {}).get("itemCount", 0), "0")

    def prediction_status(scope):
        value = (prediction.get(scope) or {}).get("status")
        return "交易时段暂不开放" if value == "not_available_in_phase" else (value or "-")

    def source_state(name):
        item = sources.get(name) or {}
        totals = item.get("totals") or {}
        return "正常" if totals.get("failures", 0) == 0 else "有失败"

    issue_html = "".join(f"<li>{text(item)}</li>" for item in issues) or "<li>当前没有需要处理的事项</li>"
    market_class = "ok-text" if market.get("available") else "bad-text"
    coverage_class = "ok-text" if coverage.get("complete") else "bad-text"
    coverage_status = "完整" if coverage.get("complete") else "需检查"
    monitor_status = "正常" if monitor.get("available") else "不可用"
    migration_status = "仍开启" if migration.get("compatReadEnabled") else "已关闭"
    today_state = prediction.get("today") or {}
    next_day_state = prediction.get("next_day") or {}
    return f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>异动查查 · 健康检查</title>
<style>
:root{{color-scheme:light;--ink:#1f2937;--muted:#667085;--line:#e5e7eb;--bg:#f6f7f9;--ok:#147d57;--bad:#b42318;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
main{{max-width:980px;margin:0 auto;padding:32px 20px 48px}}h1{{margin:0;font-size:28px;letter-spacing:0}}h2{{margin:0 0 12px;font-size:17px}}p{{margin:6px 0;color:var(--muted)}}
.hero{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}}.state{{padding:8px 14px;border-radius:999px;font-weight:700;background:#e8f6ef;color:var(--ok)}}.state.bad{{background:#fdecec;color:var(--bad)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:12px 0 20px}}.card{{padding:17px;border:1px solid var(--line);border-radius:10px;background:#fff}}.label{{font-size:13px;color:var(--muted)}}.value{{margin-top:3px;font-size:20px;font-weight:700}}.sub{{margin-top:5px;font-size:13px;color:var(--muted)}}
ul{{margin:0;padding-left:20px}}li+li{{margin-top:5px}}.ok-text{{color:var(--ok)}}.bad-text{{color:var(--bad)}}footer{{margin-top:24px;color:var(--muted);font-size:12px}}
@media(max-width:560px){{main{{padding:24px 14px 36px}}.hero{{align-items:flex-start;flex-direction:column}}}}
</style></head><body><main>
<div class=\"hero\"><div><h1>异动查查 · 健康检查</h1><p>面向运维和发布验收的服务状态摘要</p></div><div class=\"state {overall_class}\">系统{overall}</div></div>
<section class=\"grid\"><div class=\"card\"><div class=\"label\">行情计算服务</div><div class=\"value {market_class}\">{status(market.get('available'))}</div><div class=\"sub\">数据日期：{text(market.get('latestDataDate'))} · 阶段：{text(market.get('dataStage'))}</div></div>
<div class=\"card\"><div class=\"label\">数据源</div><div class=\"value\">Tushare {source_state('tushare')}</div><div class=\"sub\">实时行情：{source_state('realtime')}</div></div>
<div class=\"card\"><div class=\"label\">股票池完整性</div><div class=\"value {coverage_class}\">{text(coverage.get('total'), '-')} 只 · {coverage_status}</div><div class=\"sub\">活跃股票基础资料覆盖</div></div></section>
<section class=\"card\"><h2>预测数据集</h2><div class=\"grid\"><div><div class=\"label\">当日预测</div><div class=\"value\">{count('today')} 条</div><div class=\"sub\">目标日期：{text(today_state.get('targetTradeDate'))} · {text(prediction_status('today'))}</div></div><div><div class=\"label\">次日预测</div><div class=\"value\">{count('next_day')} 条</div><div class=\"sub\">目标日期：{text(next_day_state.get('targetTradeDate'))} · {text(prediction_status('next_day'))}</div></div></div></section>
<section class=\"card\" style=\"margin-top:12px\"><h2>重点监控</h2><p><strong>{text(monitor.get('itemCount'), '0')}</strong> 条 · {monitor_status} · 快照日期 {text(monitor.get('snapshotDate'))}</p><p>{text((monitor.get('dataQuality') or {}).get('activeSource'), '未提供来源')}</p></section>
<section class=\"card\" style=\"margin-top:12px\"><h2>需要关注的事项</h2><ul>{issue_html}</ul></section>
<footer>原始机器接口：<code>GET /health</code> · 本页面：<code>GET /health/ui</code> · 预测迁移兼容读取：{migration_status}</footer>
</main></body></html>"""


class ApiError(Exception):
    def __init__(self, code: int, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class ApiHandler(BaseHTTPRequestHandler):
    """处理小程序需要的只读 API。"""

    server_version = "StockAbnormal/0.1"

    def _respond(self, payload, status=200):
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Vary", "Accept")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _respond_html(self, body: str, status=200):
        encoded = _html_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Vary", "Accept")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-WX-SERVICE")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path in ("/health", "/health/ui"):
                market_calc_error = ""
                now = datetime.now(SHANGHAI_TZ)
                today = now.strftime("%Y%m%d")
                today_is_trade_day = False
                try:
                    market_job = SERVICE._calc_repository.job() if SERVICE._calc_repository.available else None
                    market_calc_available = bool(SERVICE._calc_repository.available)
                    today_is_trade_day = SERVICE._is_trade_day(today) if SERVICE.client.available else False
                except Exception as exc:
                    market_job = None
                    market_calc_available = False
                    market_calc_error = str(exc)[:160]
                calc_config = SERVICE._calc_repository.config
                last_failure_date = str((market_job or {}).get("last_failure_date") or "")
                last_error = market_calc_error or (market_job or {}).get("last_error", "")
                latest_data_date, latest_data_stage = SERVICE._latest_data_stage_date() if market_calc_available else ("", "")
                stock_coverage = None
                prediction_migration = None
                if market_calc_available:
                    try:
                        stock_coverage = SERVICE._calc_repository.stock_basic_coverage()
                        prediction_migration = SERVICE._calc_repository.prediction_migration_status()
                    except Exception as exc:
                        market_calc_error = market_calc_error or str(exc)[:160]
                monitor_health = OFFICIAL_MONITOR.health_stats(today_is_trade_day)
                prediction_health = SERVICE.prediction_health(today_is_trade_day)
                health_payload = {"code": 0, "data": {
                    "service": "ok",
                    "tushareConfigured": SERVICE.client.available,
                    "marketCalc": {
                        "available": market_calc_available,
                        "configured": {
                            "host": bool(calc_config.get("host")),
                            "user": bool(calc_config.get("user")),
                            "database": bool(calc_config.get("database")),
                            "driver": SERVICE._calc_repository._driver() is not None,
                        },
                        "today": today,
                        "todayIsTradeDay": today_is_trade_day,
                        "lastSuccessDate": SERVICE._confirmed_market_trade_date() if market_calc_available else (market_job or {}).get("last_success_date", ""),
                        "latestDataDate": latest_data_date,
                        "dataStage": latest_data_stage,
                        "lastError": last_error,
                        "lastFailureDate": last_failure_date,
                        "lastErrorActive": bool(last_error) and (last_failure_date == today or bool(market_calc_error)),
                        "stockBasicCoverage": stock_coverage,
                        "predictionMigration": prediction_migration,
                    },
                    "dataSources": {
                        "tushare": SERVICE.client.health_stats(),
                        "realtime": SERVICE._realtime.health_stats(),
                    },
                    "prediction": prediction_health,
                    "monitor": monitor_health,
                    # 保留旧字段，兼容旧版小程序健康检查。
                    "monitorSnapshot": monitor_health["available"],
                    "monitorRefreshing": OFFICIAL_MONITOR.refreshing,
                }, "message": "ok"}
                accept = self.headers.get("Accept", "")
                if parsed.path == "/health/ui" or (parsed.path == "/health" and "text/html" in accept and "application/json" not in accept):
                    self._respond_html(_health_ui(health_payload))
                else:
                    self._respond(health_payload)
                return
            if parsed.path == "/api/market/history-coverage":
                try:
                    offset = int(query.get("offset", ["0"])[0])
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError as exc:
                    raise ApiError(40001, "offset 和 limit 必须是整数", 400) from exc
                if offset < 0 or limit < 1:
                    raise ApiError(40001, "offset 必须不小于 0，limit 必须大于 0", 400)
                data = SERVICE.history_coverage(offset=offset, limit=limit)
            elif parsed.path == "/api/market/calculation-bases":
                try:
                    offset = int(query.get("offset", ["0"])[0])
                    limit = int(query.get("limit", ["100"])[0])
                except ValueError as exc:
                    raise ApiError(40001, "offset 和 limit 必须是整数", 400) from exc
                if offset < 0 or limit < 1 or limit > 100:
                    raise ApiError(40001, "offset 必须不小于 0，limit 必须为 1～100", 400)
                raw_codes = query.get("ts_codes", [""])[0]
                ts_codes = [item.strip().upper() for item in raw_codes.split(",") if item.strip()]
                if len(ts_codes) > 100:
                    raise ApiError(40001, "ts_codes 最多 100 只股票", 400)
                data = SERVICE.calculation_bases(ts_codes=ts_codes or None, offset=offset, limit=limit)
            elif parsed.path == "/api/market/dataset-status":
                data = SERVICE.persisted_dataset_statuses()
            elif parsed.path == "/api/market/jobs":
                data = SERVICE.persisted_jobs()
            elif parsed.path == "/api/market/status":
                latest = SERVICE.client.latest_trade_date()
                data = {"latestTradeDate": latest, "source": "tushare", "intraday": False}
            elif parsed.path == "/api/stocks/search":
                value = query.get("q", [""])[0]
                if not value:
                    raise ApiError(40001, "缺少 q 参数", 400)
                if len(value.strip()) < 1 or len(value.strip()) > 40:
                    raise ApiError(40001, "q 长度必须为 1～40 个字符", 400)
                data = SERVICE.search(value)
            elif parsed.path == "/api/stocks/detail":
                ts_code = query.get("ts_code", [""])[0]
                if not ts_code:
                    raise ApiError(40001, "缺少 ts_code 参数", 400)
                data = SERVICE.detail(ts_code)
            elif parsed.path == "/api/stocks/announcements":
                stock = SERVICE._stock(query.get("ts_code", [""])[0])
                data = ANNOUNCEMENTS.query(stock["symbol"], stock["name"])
            elif parsed.path.startswith("/api/stocks/") and parsed.path.endswith("/abnormal"):
                ts_code = parsed.path[len("/api/stocks/"):-len("/abnormal")].strip("/")
                data = SERVICE.detail(ts_code)
            elif parsed.path == "/api/predictions":
                scope = query.get("scope", ["today"])[0]
                if scope not in ("today", "next_day"):
                    raise ApiError(40001, "scope 必须是 today 或 next_day", 400)
                data = SERVICE.predictions(scope)
            elif parsed.path == "/api/monitor/announcement":
                # 严重异动期内的 PDF 由后端缓存并以内联方式返回；出监管后路径立即失效。
                monitor_key = query.get("key", [""])[0]
                file_path = OFFICIAL_MONITOR.announcement_pdf_path(monitor_key)
                if not file_path:
                    self._respond({"code": 40401, "data": None, "message": "公告文件不可用"}, status=404)
                    return
                payload = file_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Content-Disposition", "inline")
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self.wfile.write(payload)
                return
            elif parsed.path == "/api/monitor":
                # GET 只读最近一次成功快照，不启动公告采集或行情扫描。
                status = query.get("status", ["current"])[0]
                if status != "current":
                    self._respond({"code": 40401, "data": None, "message": "接口不存在"}, status=404)
                    return
                monitor_type = query.get("type", ["all"])[0]
                if monitor_type not in ("all", "risk", "severe", "delisting"):
                    raise ApiError(40001, "type 必须是 all、risk、severe 或 delisting", 400)
                data = OFFICIAL_MONITOR.read(status, monitor_type)
                # 只附加本地正式收益向量的最新日涨跌；重点监控仍保留全部监管事实，
                # 展示层据此隐藏下跌行情，不发起实时行情请求。
                for item in data.get("items", []):
                    try:
                        change = SERVICE.latest_stock_return(str(item.get("ts_code") or ""))
                    except Exception:  # noqa: BLE001 - 行情附加字段失败不应丢弃监管事实
                        change = None
                    if change:
                        item["marketChange"] = change["value"]
                        item["marketChangeDate"] = change["tradeDate"]
                # 小程序生产环境通过 callContainer 访问，统一返回可直接打开的公网代理地址。
                public_base = os.getenv("MONITOR_PUBLIC_BASE_URL", "https://flask-9a5y-319926-10-1496305218.sh.run.tcloudbase.com").rstrip("/")
                for item in data.get("items", []):
                    if item.get("pdfCached"):
                        key = quote(str(item.get("monitorKey") or ""), safe="")
                        item["sourceUrl"] = f"{public_base}/api/monitor/announcement?key={key}"
            else:
                self._respond({"code": 40401, "data": None, "message": "接口不存在"}, status=404)
                return
            self._respond({"code": 0, "data": data, "message": "ok"})
        except ApiError as exc:
            self._respond({"code": exc.code, "data": None, "message": str(exc)}, status=exc.status)
        except TushareUnavailable as exc:
            self._respond({"code": 50301, "data": None, "message": str(exc)}, status=503)
        except CalculationDataUnavailable as exc:
            self._respond({"code": 50302, "data": None, "message": str(exc)}, status=503)
        except ValueError as exc:
            code, status = (40401, 404) if "未找到" in str(exc) else (40001, 400)
            self._respond({"code": code, "data": None, "message": str(exc)}, status=status)
        except Exception:
            # 不把上游响应、token 或堆栈泄漏给小程序。
            self._respond({"code": 50001, "data": None, "message": "服务暂时不可用，请稍后重试"}, status=500)

    def do_POST(self):
        """处理不落盘的盘中行情透传和已有预测池刷新。"""

        parsed = urlparse(self.path)
        if parsed.path not in ("/api/predictions/refresh", "/api/quotes/realtime"):
            self._respond({"code": 40401, "data": None, "message": "接口不存在"}, status=404)
            return
        query = parse_qs(parsed.query)
        payload = {}
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            if body:
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("请求参数格式错误")
                for key, value in payload.items():
                    query[key] = [str(value)]
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._respond({"code": 40001, "data": None, "message": "请求参数格式错误"}, status=400)
            return
        try:
            if parsed.path == "/api/quotes/realtime":
                raw_codes = payload.get("ts_codes") if isinstance(payload, dict) else None
                if not isinstance(raw_codes, list) or not raw_codes:
                    raise ApiError(40001, "ts_codes 必须是非空数组", 400)
                codes = []
                seen = set()
                for raw_code in raw_codes:
                    code = str(raw_code or "").strip().upper()
                    if not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", code):
                        raise ApiError(40001, "ts_codes 包含非法证券代码", 400)
                    if code not in seen:
                        codes.append(code)
                        seen.add(code)
                if len(codes) >= 100:
                    raise ApiError(40001, "ts_codes 必须少于 100 个代码", 400)
                self._respond({"code": 0, "data": SERVICE.realtime_quotes(codes), "message": "ok"})
                return
            scope = query.get("scope", ["today"])[0]
            if scope not in ("today", "next_day"):
                raise ApiError(40001, "scope 必须是 today 或 next_day", 400)
            data = SERVICE.refresh_predictions(scope)
            data["refreshStatus"] = "applied" if data.get("phase") == "intraday" else "not_allowed"
            self._respond({"code": 0, "data": data, "message": "ok"})
        except ApiError as exc:
            self._respond({"code": exc.code, "data": None, "message": str(exc)}, status=exc.status)
        except TushareUnavailable as exc:
            self._respond({"code": 50301, "data": None, "message": str(exc)}, status=503)
        except ValueError as exc:
            self._respond({"code": 40001, "data": None, "message": str(exc)}, status=400)
        except Exception:
            self._respond({"code": 50001, "data": None, "message": "服务暂时不可用，请稍后重试"}, status=500)

    def log_message(self, _format, *_args):
        # 默认不记录 query，避免把股票搜索内容写入日志；需要排障时由进程管理器记录。
        return


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8787"))
    server = ThreadingHTTPServer((host, port), ApiHandler)
    SERVICE.start_scheduler()
    OFFICIAL_MONITOR.start_scheduler()
    print(f"Stock-Abnormal API listening on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        OFFICIAL_MONITOR.close()
        SERVICE.close()


if __name__ == "__main__":
    main()
