"""交易所异动公告查询（巨潮资讯官方披露平台）。"""

from __future__ import annotations

import json
import gzip
import os
import re
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed


class AnnouncementService:
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    detail_prefix = "https://static.cninfo.com.cn/"
    # 只检索交易异动公告，避免把业绩、诉讼等普通风险公告误放入重点监控。
    keywords = ("股票交易异常波动", "股票交易严重异常波动", "股票交易风险提示", "停牌核查")
    broker_alert_list = "https://wap.18.cn/article/zygg"
    broker_alert_root = "https://wap.18.cn"
    # 来源优先级：18.cn 主源 > 巨潮资讯备源。
    source_policy = {
        "broker-risk-alert": {"priority": 1, "label": "18.cn 主源"},
        "issuer-disclosure": {"priority": 2, "label": "巨潮资讯备源"},
        "zts-risk-alert": {"priority": 1, "label": "中泰证券公告"},
    }

    zts_base = "https://www.zts.com.cn"
    zts_api = zts_base + "/clientApi"
    zts_column_id = 3465  # 新闻中心 > 业务公告 > 重要通知

    @staticmethod
    def _zts_headers() -> Dict[str, str]:
        """生成中泰官网前端同口径的轻量鉴权头。"""
        try:
            import time
            from Crypto.Cipher import DES
            key = b"947dcfd3-1163-4ad7-b0f9-b68a78434406"
            value = "enRzLW5ldC1jbGllbnQ=&" + str(int(time.time() * 1000))
            padding = 8 - len(value.encode("utf-8")) % 8
            encrypted = DES.new(key[:8], DES.MODE_ECB).encrypt(
                value.encode("utf-8") + bytes([padding]) * padding
            ).hex()
            return {
                "Authorization": encrypted,
                "X-App-Key": "50afb4aceb38497d9e95ee6c9d3a38df",
                "User-Agent": "Stock-Abnormal/1.0",
                "Referer": "https://www.zts.com.cn/news/business/announce",
            }
        except Exception as exc:  # pragma: no cover - dependency failure is source degradation
            raise RuntimeError(f"中泰源鉴权不可用: {exc}") from exc

    def _zts_request(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        request = Request(
            self.zts_api + path + "?" + urlencode(params),
            headers=self._zts_headers(),
        )
        return json.loads(urlopen(request, timeout=15).read().decode("utf-8"))

    @staticmethod
    def _html_text(value: str) -> str:
        from html import unescape
        return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()

    def query_zts_risk_alerts(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """读取中泰业务公告中的交易风险提示和退市整理风险警示。"""
        try:
            rows: List[Dict[str, Any]] = []
            page = 1
            while page <= 5:
                response = self._zts_request("/article/getArticleList", {
                    "columnId": self.zts_column_id, "title": "", "pageSize": 100,
                    "pageNum": page, "PublishTo": "portal",
                })
                page_rows = response.get("rows") or []
                rows.extend(page_rows)
                if len(rows) >= int(response.get("total") or 0) or len(page_rows) < 100:
                    break
                page += 1
            candidates = [row for row in rows if start_date <= str(row.get("publishTime", ""))[:10] <= end_date and (
                "交易风险提示" in str(row.get("title") or "")
                or "退市整理" in str(row.get("title") or "")
                or "退市风险警示" in str(row.get("title") or "")
            )]
            items: List[Dict[str, Any]] = []
            failures: List[str] = []
            for row in candidates:
                try:
                    detail = self._zts_request("/article/getArticleDetail", {"id": row.get("id")})
                    article = detail.get("data") or row
                    title = str(article.get("title") or row.get("title") or "").strip()
                    body = self._html_text(str(article.get("content") or ""))
                    code_match = re.search(r"[（(](\d{6})[）)]", title)
                    if not code_match:
                        continue
                    code = code_match.group(1)
                    name_match = re.search(r"[“\"]([^“”\"]+?)[”\"][（(]" + code, title)
                    name = name_match.group(1).strip() if name_match else title.split(code)[0].strip(" 关于")
                    is_delisting = "退市整理" in title or "退市风险警示" in title
                    items.append({
                        "title": title, "body": body, "date": str(article.get("publishTime") or row.get("publishTime"))[:10],
                        "stockCode": code, "stockName": name,
                        "url": f"{self.zts_base}/news/business/announce?articleId={article.get('id') or row.get('id')}",
                        "source": "中泰证券", "sourceType": "zts-risk-alert", "sourceRole": "zts_primary",
                        "sourcePriority": self.source_policy["zts-risk-alert"]["priority"],
                        "sourceLabel": self.source_policy["zts-risk-alert"]["label"],
                        "riskToneHint": "delisting-warning" if is_delisting else "ordinary",
                    })
                except Exception as exc:  # noqa: BLE001 - one detail must not discard the source
                    failures.append(str(row.get("id") or exc))
            return {"items": items, "available": True, "partial": bool(failures), "error": "; ".join(failures[:3]),
                    "source": "中泰证券", "sourceUrl": self.zts_base + "/news/business/announce"}
        except Exception as exc:  # noqa: BLE001 - optional source
            return {"items": [], "available": False, "partial": True, "error": str(exc), "source": "中泰证券"}

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = Request(self.endpoint, data=urlencode(payload).encode("utf-8"), headers={
            "User-Agent": "Stock-Abnormal/1.0", "Referer": "https://www.cninfo.com.cn/",
            "Origin": "https://www.cninfo.com.cn",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        })
        return json.loads(urlopen(request, timeout=10).read().decode("utf-8"))

    @staticmethod
    def _announcement_date(row: Dict[str, Any]) -> str:
        relative_url = str(row.get("adjunctUrl") or "")
        date_match = re.search(r"finalpage/(\d{4}-\d{2}-\d{2})/", relative_url)
        if date_match:
            return date_match.group(1)
        try:
            return datetime.fromtimestamp(int(row.get("announcementTime")) / 1000).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            return ""

    def query_market(self, start_date: str, end_date: str, page_size: int = 50) -> Dict[str, Any]:
        """按日期批量获取全市场异动相关公告，并按公告原文 URL 去重。"""

        found: Dict[str, Dict[str, Any]] = {}
        errors: List[str] = []
        # 巨潮的 fulltext 查询按交易所栏目分别执行；北交所使用 bse，
        # 否则 8/4/9 开头的北交所股票（例如世纪数码 920229）会漏掉。
        for column in ("sse", "szse", "bse"):
            for keyword in self.keywords:
                page = 1
                while page <= 8:
                    payload = {
                        "pageNum": page, "pageSize": page_size, "column": column, "tabName": "fulltext",
                        "plate": "", "stock": "", "searchkey": keyword, "secid": "", "category": "",
                        "trade": "", "seDate": f"{start_date}~{end_date}",
                    }
                    try:
                        response = self._request(payload)
                    except Exception:
                        errors.append(f"{column}:{keyword}")
                        break
                    rows = response.get("announcements") or []
                    for row in rows:
                        title = str(row.get("announcementTitle") or row.get("shortTitle") or "").strip()
                        code = str(row.get("secCode") or "").strip()
                        # 搜索结果可能包含相近词，最终仍以标题白名单过滤。
                        if not code or not any(item in title for item in self.keywords):
                            continue
                        relative_url = str(row.get("adjunctUrl") or "")
                        url = self.detail_prefix + relative_url.lstrip("/") if relative_url else ""
                        key = url or f"{code}:{self._announcement_date(row)}:{title}"
                        found[key] = {
                            "title": title,
                            "date": self._announcement_date(row),
                            "stockCode": code,
                            "stockName": str(row.get("secName") or ""),
                            "url": url,
                            "source": "巨潮资讯",
                            "exchangeColumn": column,
                        }
                    if not response.get("hasMore") or not rows:
                        break
                    page += 1
        return {
            "items": sorted(found.values(), key=lambda item: (item["date"], item["stockCode"]), reverse=True),
            "available": not errors,
            "partial": bool(errors),
            "failedKeywords": sorted(set(errors)),
            "source": "巨潮资讯",
            "sourceUrl": self.endpoint,
        }

    @staticmethod
    def extract_pdf_text(url: str) -> str:
        """只为严重异动候选提取公告正文，失败时返回空文本并保留标题结果。"""

        if not url:
            return ""

        try:
            from pypdf import PdfReader
            request = Request(url, headers={"User-Agent": "Stock-Abnormal/1.0", "Referer": "https://www.cninfo.com.cn/"})
            content = urlopen(request, timeout=12).read()
            reader = PdfReader(BytesIO(content))
            return "\n".join((page.extract_text() or "") for page in reader.pages[:20])
        except Exception:
            return ""

    class _AlertListParser(HTMLParser):
        """读取 18.cn 重要公告列表中的标题、日期和详情路径。"""

        def __init__(self) -> None:
            super().__init__()
            self.items: List[Dict[str, str]] = []
            self.current: Dict[str, str] = {}
            self.in_item = False
            self.in_paragraph = False
            self.paragraph = ""

        def handle_starttag(self, tag: str, attrs) -> None:
            values = dict(attrs)
            if tag == "li" and values.get("url", "").startswith("/article/detail/"):
                self.current = {"path": values["url"]}
                self.in_item = True
            elif tag == "p" and self.in_item:
                self.in_paragraph = True
                self.paragraph = ""

        def handle_data(self, data: str) -> None:
            if self.in_paragraph:
                self.paragraph += data

        def handle_endtag(self, tag: str) -> None:
            if tag == "p" and self.in_paragraph:
                value = self.paragraph.strip()
                if value:
                    self.current["title" if "title" not in self.current else "date"] = value
                self.in_paragraph = False
            elif tag == "li" and self.in_item:
                if self.current.get("title") and self.current.get("date"):
                    self.items.append(self.current)
                self.current = {}
                self.in_item = False

    class _TextParser(HTMLParser):
        """提取详情页正文文本，供关键监管原文匹配。"""

        def __init__(self) -> None:
            super().__init__()
            self.parts: List[str] = []

        def handle_data(self, data: str) -> None:
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self.parts.append(text)

    def query_broker_risk_alerts(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """获取 18.cn 东方财富证券重要公告正文和链接，分类由监控服务统一完成。"""

        try:
            request = Request(self.broker_alert_list, headers={"User-Agent": "Mozilla/5.0"})
            page = urlopen(request, timeout=12).read().decode("utf-8", "replace")
            parser = self._AlertListParser()
            parser.feed(page)
        except Exception as exc:  # noqa: BLE001 - 上游错误由快照任务统一处理
            return {"items": [], "available": False, "error": str(exc), "source": "东方财富证券 18.cn"}

        items: List[Dict[str, Any]] = []
        failures: List[str] = []

        def fetch_detail(path: str, title_hint: str = "", date_hint: str = "") -> Dict[str, Any]:
            """读取详情页并处理 18.cn 偶发的 gzip 响应。"""

            url = self.broker_alert_root + path
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            response = urlopen(request, timeout=12)
            content = response.read()
            if content[:2] == b"\x1f\x8b" or response.headers.get("Content-Encoding", "").lower() == "gzip":
                content = gzip.decompress(content)
            html = content.decode("utf-8", "replace")
            body_parser = self._TextParser()
            body_parser.feed(html)
            body = " ".join(body_parser.parts)
            title_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
            title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else title_hint
            date_match = re.search(r"<h5[^>]*>\s*(\d{4}-\d{2}-\d{2})", html, re.I)
            announcement_date = date_match.group(1) if date_match else date_hint
            code_match = re.search(r"[（(](\d{6})[）)]", title)
            if not title or not announcement_date or not code_match:
                raise ValueError("18.cn 详情页缺少标题、日期或证券代码")
            name_match = re.search(r"关于[“\"](.+?)[（(]" + code_match.group(1) + r"[）)]", title)
            if not name_match:
                raise ValueError("18.cn 详情页缺少证券名称")
            return {
                "title": title, "date": announcement_date, "stockCode": code_match.group(1),
                "stockName": name_match.group(1), "url": url, "source": "东方财富证券 18.cn",
                "sourceType": "broker-risk-alert", "sourceRole": "broker_primary",
                "sourcePriority": self.source_policy["broker-risk-alert"]["priority"],
                "sourceLabel": self.source_policy["broker-risk-alert"]["label"], "body": body,
            }

        for row in parser.items:
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", row["date"])
            if not date_match or not (start_date <= date_match.group(0) <= end_date):
                continue
            try:
                items.append(fetch_detail(row["path"], row["title"], date_match.group(0)))
            except Exception as exc:  # noqa: BLE001 - 部分详情失败时禁止悄悄发布不完整结果
                failures.append(f"{row.get('path', '')}: {exc}")

        # 列表页只保留最近 20 条。补读一小段相邻历史 ID，避免仍在 10 个
        # 交易日监管期内的公告因滚出列表而永久消失。失败的 404/空页只计入
        # 部分失败，不影响已成功读取的公告发布。
        history_ids = []
        current_ids = [int(value) for row in parser.items for value in re.findall(r"/article/detail/818/(\d+)", row.get("path", ""))]
        if current_ids and os.getenv("BROKER_HISTORY_SCAN", "1") != "0":
            floor = max(1, min(current_ids) - 80)
            history_ids = list(range(min(current_ids) - 1, floor - 1, -1))

        def fetch_history(identifier: int) -> Optional[Dict[str, Any]]:
            try:
                item = fetch_detail(f"/article/detail/818/{identifier}")
                if start_date <= item["date"] <= end_date:
                    return item
            except Exception:
                return None
            return None

        if history_ids:
            with ThreadPoolExecutor(max_workers=12) as executor:
                futures = [executor.submit(fetch_history, identifier) for identifier in history_ids]
                for future in as_completed(futures):
                    item = future.result()
                    if item:
                        items.append(item)

        deduped = {item["url"]: item for item in items if item.get("url")}
        return {
            "items": sorted(deduped.values(), key=lambda item: (item["date"], item["stockCode"], item["url"]), reverse=True),
            "available": True,
            "partial": bool(failures),
            "error": "; ".join(failures[:3]),
            "source": "东方财富证券 18.cn", "sourceUrl": self.broker_alert_list,
        }

    def query(self, symbol: str, name: str = "", limit: int = 10) -> Dict[str, Any]:
        symbol = str(symbol or "").strip()
        if not symbol:
            return {"items": [], "source": "巨潮资讯", "sourceUrl": self.endpoint, "available": True}
        column = "bse" if symbol.startswith(("4", "8", "9")) else "szse" if symbol.startswith(("0", "2", "3")) else "sse"
        payload = {
            "pageNum": 1, "pageSize": max(20, min(int(limit) * 5, 50)),
            "column": column,
            "tabName": "fulltext", "plate": "", "stock": "", "searchkey": symbol,
            "secid": "", "category": "", "trade": "", "seDate": "",
        }
        try:
            response = self._request(payload)
        except Exception:
            return {"items": [], "source": "巨潮资讯", "sourceUrl": self.endpoint, "available": False}
        items: List[Dict[str, Any]] = []
        for row in response.get("announcements") or []:
            code = str(row.get("secCode") or "")
            title = str(row.get("announcementTitle") or row.get("shortTitle") or "").strip()
            if code != symbol or not any(keyword in title for keyword in self.keywords):
                continue
            relative_url = str(row.get("adjunctUrl") or "")
            announcement_date = self._announcement_date(row)
            items.append({
                "title": title, "date": announcement_date, "stockCode": code,
                "stockName": str(row.get("secName") or name or ""),
                "type": "严重异常波动" if "严重异常波动" in title else "异常波动 / 风险提示",
                "url": self.detail_prefix + relative_url.lstrip("/") if relative_url else "",
                "source": "巨潮资讯",
            })
            if len(items) >= limit:
                break
        return {"items": items, "source": "巨潮资讯", "sourceUrl": self.endpoint, "available": True}
