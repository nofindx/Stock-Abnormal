"""异动查查 HTTP API 入口。

运行：
    TUSHARE_TOKEN=... python3 -m server.app

接口统一返回 {code, data, message}，便于小程序请求层处理加载、失败和降级状态。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .services.market_service import MarketService, SHANGHAI_TZ
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

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
            if parsed.path == "/health":
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
                monitor_health = OFFICIAL_MONITOR.health_stats(today_is_trade_day)
                prediction_health = SERVICE.prediction_health(today_is_trade_day)
                self._respond({"code": 0, "data": {
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
                    },
                    "dataSources": {
                        "tushare": SERVICE.client.health_stats(),
                        "realtime": SERVICE._realtime.health_stats(),
                    },
                    "prediction": prediction_health,
                    "intradayState": {
                        "backend": "redis" if SERVICE._intraday_store.shared else "local_fallback",
                        "shared": SERVICE._intraday_store.shared,
                        "ttlSeconds": SERVICE._intraday_store.TTL_SECONDS,
                        "maxPayloadBytes": SERVICE._intraday_store.MAX_PAYLOAD_BYTES,
                    },
                    "monitor": monitor_health,
                    # 保留旧字段，兼容旧版小程序健康检查。
                    "monitorSnapshot": monitor_health["available"],
                    "monitorRefreshing": OFFICIAL_MONITOR.refreshing,
                }, "message": "ok"})
                return
            if parsed.path == "/api/market/status":
                latest = SERVICE.client.latest_trade_date()
                data = {"latestTradeDate": latest, "source": "tushare", "intraday": False}
            elif parsed.path == "/api/stocks/search":
                value = query.get("q", [""])[0]
                if not value:
                    raise ApiError(40001, "缺少 q 参数", 400)
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
                data = OFFICIAL_MONITOR.read(status, query.get("type", ["all"])[0])
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
        except ValueError as exc:
            code, status = (40401, 404) if "未找到" in str(exc) else (40001, 400)
            self._respond({"code": code, "data": None, "message": str(exc)}, status=status)
        except Exception:
            # 不把上游响应、token 或堆栈泄漏给小程序。
            self._respond({"code": 50001, "data": None, "message": "服务暂时不可用，请稍后重试"}, status=500)

    def do_POST(self):
        """预测刷新只处理已有池的盘中临时行情，不触发全市场后台计算。"""

        parsed = urlparse(self.path)
        if parsed.path != "/api/predictions/refresh":
            self._respond({"code": 40401, "data": None, "message": "接口不存在"}, status=404)
            return
        query = parse_qs(parsed.query)
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
