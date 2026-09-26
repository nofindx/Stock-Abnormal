"""交易所异动公告查询（巨潮资讯官方披露平台）。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AnnouncementService:
    endpoint = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    detail_prefix = "https://static.cninfo.com.cn/"
    keywords = ("异常波动", "严重异常波动", "风险提示", "停牌核查", "重点监控")

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
        request = Request(self.endpoint, data=urlencode(payload).encode("utf-8"), headers={
            "User-Agent": "Stock-Abnormal/1.0", "Referer": "https://www.cninfo.com.cn/",
            "Origin": "https://www.cninfo.com.cn",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        })
        try:
            response = json.loads(urlopen(request, timeout=8).read().decode("utf-8"))
        except Exception:
            return {"items": [], "source": "巨潮资讯", "sourceUrl": self.endpoint, "available": False}
        items: List[Dict[str, Any]] = []
        for row in response.get("announcements") or []:
            code = str(row.get("secCode") or "")
            title = str(row.get("announcementTitle") or row.get("shortTitle") or "").strip()
            if code != symbol or not any(keyword in title for keyword in self.keywords):
                continue
            relative_url = str(row.get("adjunctUrl") or "")
            # 公告原文路径中的日期是披露日期，优先于时间戳，避免 UTC/本地时区偏移一天。
            date_match = re.search(r"finalpage/(\d{4}-\d{2}-\d{2})/", relative_url)
            if date_match:
                announcement_date = date_match.group(1)
            else:
                try:
                    announcement_date = datetime.fromtimestamp(int(row.get("announcementTime")) / 1000).strftime("%Y-%m-%d")
                except (TypeError, ValueError, OSError):
                    announcement_date = ""
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
