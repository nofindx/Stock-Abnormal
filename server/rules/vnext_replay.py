"""Deterministic replay helpers for confirmed announcement facts.

This is intentionally separate from the frozen legacy scanner: vNext counts
confirmed 3-day ordinary facts, then applies dated reset boundaries. It never
turns an行情 candidate into a regulatory event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class ConfirmedEvent:
    fact_id: str
    end_date: str
    direction: str
    fact_type: str = "ordinary_3d"


@dataclass(frozen=True)
class ResetBoundary:
    effective_date: str
    severity: str  # ordinary or severe


def replay_same_direction(events: Iterable[Mapping[str, object]], *, target_date: str,
                          window_dates: List[str], threshold: int,
                          boundaries: Iterable[Mapping[str, object]] = ()) -> dict:
    """Replay exact event counts for one target date.

    Events are deduplicated by ``fact_id``. A severe boundary clears events
    before its effective date; an ordinary boundary only changes the 3-day
    price baseline and does not remove same-direction events.
    """

    valid_dates = set(window_dates)
    parsed = []
    seen = set()
    for raw in events:
        fact_id = str(raw.get("fact_id") or raw.get("factId") or "")
        if not fact_id or fact_id in seen:
            continue
        fact_type = str(raw.get("fact_type") or raw.get("factType") or "ordinary_3d")
        direction = str(raw.get("direction") or "")
        end_date = str(raw.get("end_date") or raw.get("fact_end_date") or "")
        if fact_type != "ordinary_3d" or direction not in {"up", "down"}:
            continue
        seen.add(fact_id)
        parsed.append(ConfirmedEvent(fact_id, end_date, direction, fact_type))

    severe_dates = sorted(
        str(item.get("effective_date") or "") for item in boundaries
        if str(item.get("severity") or "") == "severe"
    )
    clear_before = max((value for value in severe_dates if value <= target_date), default="")
    if clear_before:
        parsed = [item for item in parsed if item.end_date >= clear_before]
    parsed = [item for item in parsed if item.end_date in valid_dates and item.end_date <= target_date]
    up = sum(item.direction == "up" for item in parsed)
    down = sum(item.direction == "down" for item in parsed)
    return {
        "target_date": target_date,
        "up_count": up,
        "down_count": down,
        "threshold": int(threshold),
        "up_triggered": up >= threshold,
        "down_triggered": down >= threshold,
        "event_ids": [item.fact_id for item in sorted(parsed, key=lambda value: (value.end_date, value.fact_id))],
        "exact_count": True,
    }


def replay_reset_scope(boundaries: Iterable[Mapping[str, object]], target_date: str) -> Tuple[str, ...]:
    """Return the metrics whose latest effective reset applies on target date."""

    latest = [item for item in boundaries if str(item.get("effective_date") or "") <= target_date]
    if not latest:
        return ()
    severe = any(str(item.get("severity") or "") == "severe" for item in latest)
    return ("ordinary_deviation_3d", "same_direction_10d", "severe_deviation_10d", "severe_deviation_30d") if severe else ("ordinary_deviation_3d",)
