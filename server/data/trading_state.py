"""Classify the security independently of realtime quote quality."""
import re

from server.domain.contracts import TradeState


_TIMING = re.compile(r"([0-2]\d:[0-5]\d)(?::[0-5]\d)?\s*[-—~至]\s*([0-2]\d:[0-5]\d)(?::[0-5]\d)?")


def resolve_trading_state(list_status, delist_date, today, when, rows, *, known_recent_trade):
    if list_status == "D" and delist_date and delist_date <= today:
        return TradeState.TERMINATED, "终止上市已生效"
    if list_status != "L":
        return TradeState.UNKNOWN, "证券上市状态待核实"
    if rows is None:
        if known_recent_trade == today:
            return TradeState.NORMAL, "已有当日交易记录"
        return TradeState.UNKNOWN, "停复牌信息暂不可用"
    days = sorted({day for day, kind, timing in rows if kind in {"S", "R"}})
    for day in reversed(days):
        events = [(kind, timing) for event_day, kind, timing in rows if event_day == day]
        resumptions = any(kind == "R" for kind, _ in events)
        suspensions = [timing for kind, timing in events if kind == "S"]
        for timing in suspensions:
            intervals = _TIMING.findall(timing)
            full_session = all(any(start <= opened and end >= closed for start, end in intervals)
                               for opened, closed in (("09:30", "11:30"), ("13:00", "15:00")))
            if full_session and not resumptions:
                return TradeState.SUSPENDED, "已确认停牌"
            if day == today:
                if intervals and any(start <= when < end for start, end in intervals):
                    return TradeState.SUSPENDED, "盘中临时停牌"
                if not intervals and "临时" in timing and not resumptions:
                    return TradeState.SUSPENDED, "临时停牌"
        if any(not _TIMING.search(timing) and "临时" not in timing for timing in suspensions):
            if resumptions:
                if known_recent_trade == today:
                    return TradeState.NORMAL, "已有当日交易记录"
                return TradeState.UNKNOWN, "同日停复牌的先后待核实"
            return TradeState.SUSPENDED, "已确认停牌"
        if resumptions:
            return TradeState.NORMAL, "已确认复牌"
    if known_recent_trade:
        return TradeState.NORMAL, "有交易记录且未见新的停牌"
    return TradeState.UNKNOWN, "缺少可确认的交易状态证据"
