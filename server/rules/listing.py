"""IPO rule applicability, independent of announcement facts and price arithmetic."""

from typing import Any, Dict, Iterable


IPO_NO_LIMIT_SESSIONS = {
    "主板": 5, "创业板": 5, "科创板": 5, "北交所": 1,
}
IPO_NOT_APPLICABLE = "新股无涨跌幅限制期间，四项预警暂不适用"


def listing_applicability(board: str, listing_date: str, target_date: str,
                          open_dates: Iterable[str]) -> Dict[str, Any]:
    """The first limited session contributes its full return; it is not a zero reset."""
    count = IPO_NO_LIMIT_SESSIONS.get(board)
    sessions = sorted({day for day in open_dates if day >= listing_date})
    if not count or listing_date not in sessions or len(sessions) <= count:
        return {"applicable": None, "reason": "LISTING_CALENDAR_UNAVAILABLE",
                "message": "新股交易日历未确认，规则适用日期暂不可用", "effectiveFrom": ""}
    effective = sessions[count]
    applicable = target_date >= effective
    return {
        "applicable": applicable, "reason": "" if applicable else "IPO_NO_PRICE_LIMIT",
        "message": "" if applicable else IPO_NOT_APPLICABLE,
        "listingDate": listing_date, "effectiveFrom": effective,
        "noLimitSessions": count, "targetTradeDate": target_date,
    }
