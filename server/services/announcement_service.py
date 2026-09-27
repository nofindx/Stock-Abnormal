"""交易所异动公告查询（巨潮资讯官方披露平台）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AnnouncementService:
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    detail_prefix = "https://static.cninfo.com.cn/"
    # 只检索交易异动公告，避免把业绩、诉讼等普通风险公告误放入监管池。
    keywords = ("股票交易异常波动", "股票交易严重异常波动", "股票交易风险提示", "停牌核查")

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
        # 巨潮的 fulltext 查询按交易所栏目分别执行；单查 szse 会漏掉上交所披露。
        for column in ("sse", "szse"):
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

    def query(self, symbol: str, name: str = "", limit: int = 10) -> Dict[str, Any]:
        symbol = str(symbol or "").strip()
        if not symbol:
            return {"items": [], "source": "巨潮资讯", "sourceUrl": self.endpoint, "available": True}
        payload = {
            "pageNum": 1, "pageSize": max(20, min(int(limit) * 5, 50)),
            "column": "szse" if symbol.startswith(("0", "2", "3")) else "sse",
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
