"""异动查查 HTTP API 入口。

运行：
    TUSHARE_TOKEN=... python3 -m server.app

接口统一返回 {code, data, message}，便于小程序请求层处理加载、失败和降级状态。
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .services.market_service import MarketService
from .services.announcement_service import AnnouncementService
from .services.monitor_service import OfficialMonitorService
from .services.tushare_client import TushareUnavailable


SERVICE = MarketService()
ANNOUNCEMENTS = AnnouncementService()
# 监管池是独立的公告快照链路，不再从全市场预测结果推导。
OFFICIAL_MONITOR = OfficialMonitorService(SERVICE.client, ANNOUNCEMENTS)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


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
                try:
                    market_job = SERVICE._calc_repository.job() if SERVICE._calc_repository.available else None
                    market_calc_available = bool(SERVICE._calc_repository.available)
                except Exception as exc:
                    market_job = None
                    market_calc_available = False
                    market_calc_error = str(exc)[:160]
                calc_config = SERVICE._calc_repository.config
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
                        "lastSuccessDate": (market_job or {}).get("last_success_date", ""),
                        "lastError": market_calc_error or (market_job or {}).get("last_error", ""),
                    },
                    "monitorSnapshot": bool(OFFICIAL_MONITOR.repository.active_snapshot()),
                    "monitorRefreshing": OFFICIAL_MONITOR.refreshing,
                }, "message": "ok"})
                return
            if parsed.path == "/api/market/status":
                latest = SERVICE.client.latest_trade_date()
                data = {"latestTradeDate": latest, "source": "tushare", "intraday": False}
            elif parsed.path == "/api/stocks/search":
                data = SERVICE.search(query.get("q", [""])[0])
            elif parsed.path == "/api/stocks/detail":
                data = SERVICE.detail(query.get("ts_code", [""])[0])
            elif parsed.path == "/api/stocks/announcements":
                stock = SERVICE._stock(query.get("ts_code", [""])[0])
                data = ANNOUNCEMENTS.query(stock["symbol"], stock["name"])
            elif parsed.path.startswith("/api/stocks/") and parsed.path.endswith("/abnormal"):
                ts_code = parsed.path[len("/api/stocks/"):-len("/abnormal")].strip("/")
                data = SERVICE.detail(ts_code)
            elif parsed.path == "/api/predictions":
                data = SERVICE.predictions(query.get("scope", ["today"])[0])
            elif parsed.path == "/api/monitor/announcement":
                # 严重异动期内的 PDF 由后端缓存并以内联方式返回；出监管后路径立即失效。
                monitor_key = query.get("key", [""])[0]
                file_path = OFFICIAL_MONITOR.announcement_pdf_path(monitor_key)
                if not file_path:
                    self._respond({"code": 404, "data": None, "message": "公告文件不可用"}, status=404)
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
                    self._respond({"code": 404, "data": None, "message": "接口不存在"}, status=404)
                    return
                data = OFFICIAL_MONITOR.read(status, query.get("type", ["all"])[0])
                # 小程序生产环境通过 callContainer 访问，统一返回可直接打开的公网代理地址。
                public_base = os.getenv("MONITOR_PUBLIC_BASE_URL", "https://flask-9a5y-319926-10-1496305218.sh.run.tcloudbase.com").rstrip("/")
                for item in data.get("items", []):
                    if item.get("pdfCached"):
                        key = quote(str(item.get("monitorKey") or ""), safe="")
                        item["sourceUrl"] = f"{public_base}/api/monitor/announcement?key={key}"
            else:
                self._respond({"code": 404, "data": None, "message": "接口不存在"}, status=404)
                return
            self._respond({"code": 0, "data": data, "message": "ok"})
        except (ValueError, TushareUnavailable) as exc:
            self._respond({"code": 503, "data": None, "message": str(exc)}, status=503)
        except Exception:
            # 不把上游响应、token 或堆栈泄漏给小程序。
            self._respond({"code": 500, "data": None, "message": "服务暂时不可用，请稍后重试"}, status=500)

    def do_POST(self):
        """预测刷新先采用同步快照，后续可替换为后台任务和 snapshot_id。"""

        parsed = urlparse(self.path)
        if parsed.path != "/api/predictions/refresh":
            self._respond({"code": 404, "data": None, "message": "接口不存在"}, status=404)
            return
        query = parse_qs(parsed.query)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            if body:
                payload = json.loads(body.decode("utf-8"))
                for key, value in payload.items():
                    query[key] = [str(value)]
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._respond({"code": 400, "data": None, "message": "请求参数格式错误"}, status=400)
            return
        try:
            scope = query.get("scope", ["today"])[0]
            data = SERVICE.refresh_predictions(scope)
            self._respond({"code": 0, "data": data, "message": "ok"})
        except (ValueError, TushareUnavailable) as exc:
            self._respond({"code": 503, "data": None, "message": str(exc)}, status=503)
        except Exception:
            self._respond({"code": 500, "data": None, "message": "服务暂时不可用，请稍后重试"}, status=500)

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
