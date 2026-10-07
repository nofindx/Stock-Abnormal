"""交易所异动公告查询（巨潮资讯官方披露平台）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from typing import Any, Dict, List
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AnnouncementService:
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    detail_prefix = "https://static.cninfo.com.cn/"
    # 只检索交易异动公告，避免把业绩、诉讼等普通风险公告误放入监管池。
    keywords = ("股票交易异常波动", "股票交易严重异常波动", "股票交易风险提示", "停牌核查")
    broker_alert_list = "https://wap.18.cn/article/zygg"
    broker_alert_root = "https://wap.18.cn"
    # 来源优先级：18.cn 主源 > 巨潮资讯备源。
    source_policy = {
        "broker-risk-alert": {"priority": 1, "label": "18.cn 主源"},
        "issuer-disclosure": {"priority": 2, "label": "巨潮资讯备源"},
    }

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
        for row in parser.items:
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", row["date"])
            code_match = re.search(r"[（(](\d{6})[）)]", row["title"])
            if not date_match or not code_match or not (start_date <= date_match.group(0) <= end_date):
                continue
            url = self.broker_alert_root + row["path"]
            try:
                detail_request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                detail = urlopen(detail_request, timeout=12).read().decode("utf-8", "replace")
                body_parser = self._TextParser()
                body_parser.feed(detail)
                body = " ".join(body_parser.parts)
            except Exception as exc:  # noqa: BLE001 - 部分详情失败时禁止悄悄发布不完整结果
                return {"items": items, "available": False, "error": str(exc), "source": "东方财富证券 18.cn"}
            # 18.cn 只负责提供正文和链接；是否纳入及风险类型由监控服务统一按正文分类。
            # ETF、基金、北交所等非核心板块标的也必须保留，前端只做视觉弱化。
            code = code_match.group(1)
            if not re.fullmatch(r"\d{6}", code):
                continue
            name_match = re.search(r"关于[“\"](.+?)[（(]" + code + r"[）)]", row["title"])
            if not name_match:
                continue
            items.append({
                "title": row["title"], "date": date_match.group(0),
                "stockCode": code, "stockName": name_match.group(1),
                "url": url, "source": "东方财富证券 18.cn",
                "sourceType": "broker-risk-alert", "sourceRole": "broker_primary",
                "sourcePriority": self.source_policy["broker-risk-alert"]["priority"],
                "sourceLabel": self.source_policy["broker-risk-alert"]["label"], "body": body,
            })
        return {"items": items, "available": True, "source": "东方财富证券 18.cn", "sourceUrl": self.broker_alert_list}

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
