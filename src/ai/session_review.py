"""Post-session review — computes due-ness and per-type review blocks.

Strength sessions wait STRENGTH_BUFFER_HOURS after the session ends because
Bodhi corrects sets/weights in Garmin Connect within 1-2 hours; other types
carry no manually-edited data and review on the next sync."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..db.models import Database

EXCLUDED_TYPES = {"walking"}
MIN_DURATION_MIN = 15
STRENGTH_BUFFER_HOURS = 2.0
LOOKBACK_DAYS = 3


def _activity_end(activity: dict[str, Any]) -> datetime | None:
    start_raw = activity.get("start_time")
    if not start_raw:
        return None
    try:
        start = datetime.fromisoformat(str(start_raw))
    except ValueError:
        return None
    return start + timedelta(minutes=activity.get("duration_min") or 0)


def _buffer_hours(activity_type: str) -> float:
    return STRENGTH_BUFFER_HOURS if activity_type == "strength" else 0.0


def pending_reviews(db: Database, now: datetime | None = None) -> list[dict[str, Any]]:
    """Reviewable, unreviewed, due activities — oldest first."""
    now = now or datetime.now()
    due: list[dict[str, Any]] = []
    for activity in db.get_recent_activities(days=LOOKBACK_DAYS):
        activity_type = str(activity.get("type") or "")
        if activity_type in EXCLUDED_TYPES or activity_type == "":
            continue
        if (activity.get("duration_min") or 0) < MIN_DURATION_MIN:
            continue
        if db.get_last_notification(f"session_review_{activity['id']}") is not None:
            continue
        end = _activity_end(activity)
        if end is None:
            continue
        if now >= end + timedelta(hours=_buffer_hours(activity_type)):
            due.append(activity)
    return sorted(due, key=lambda a: str(a.get("start_time")))
