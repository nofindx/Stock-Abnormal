"""One timestamp and pairing policy for all realtime market reads."""
from datetime import datetime, timedelta, timezone
from math import isfinite
import re

SHANGHAI_TZ = timezone(timedelta(hours=8))
MAX_QUOTE_SKEW_SECONDS = 60
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:[0-5]\d)?")


def quote_time(quote):
    if not isinstance(quote, dict):
        return None
    value = str(quote.get("updatedAt") or "")
    if not _TIMESTAMP.fullmatch(value):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=SHANGHAI_TZ) if stamp.tzinfo is None else stamp.astimezone(SHANGHAI_TZ)
    except (ValueError, OverflowError):
        return None


def usable_quote(quote, *, now, earliest_day=""):
    stamp = quote_time(quote)
    if stamp is None or stamp > now or stamp.strftime("%Y%m%d") < earliest_day:
        return False
    try:
        price = float(quote.get("current"))
        if not isfinite(price) or price <= 0:
            return False
        if "preClose" in quote and (not isfinite(float(quote["preClose"])) or float(quote["preClose"]) <= 0):
            return False
        return "pctChg" not in quote or isfinite(float(quote["pctChg"]))
    except (TypeError, ValueError, OverflowError):
        return False


def comparable_quotes(stock, index):
    left, right = quote_time(stock), quote_time(index)
    if not isinstance(stock, dict) or not isinstance(index, dict):
        return False
    source = str(stock.get("source") or "").strip()
    return bool(left and right and source and source == str((index or {}).get("source") or "").strip()
                and left.date() == right.date() and abs((left - right).total_seconds()) <= MAX_QUOTE_SKEW_SECONDS)
