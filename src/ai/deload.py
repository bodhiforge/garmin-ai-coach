"""Week-granularity fatigue detection. Fires when load keeps climbing while
recovery markers degrade — the signal that a deload week is due."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..db.models import Database

DELOAD_WEEKS = 4
DELOAD_HRV_RATIO = 0.95
DELOAD_READINESS_DROP = 10.0
DELOAD_COOLDOWN_HOURS = 28 * 24


def _weekly_loads(db: Database) -> list[float]:
    """Corrected load per 7-day bucket, index 0 = most recent week."""
    buckets = [0.0] * DELOAD_WEEKS
    today = date.today()
    for activity in db.get_recent_activities(days=DELOAD_WEEKS * 7):
        days_ago = (today - date.fromisoformat(str(activity["date"]))).days
        bucket = min(days_ago // 7, DELOAD_WEEKS - 1)
        buckets[bucket] += db.get_corrected_load(
            activity["id"], activity.get("training_load") or 0.0
        )
    return [round(value, 1) for value in buckets]


def _mean(values: list[float]) -> float | None:
    cleaned = [v for v in values if v is not None]
    return sum(cleaned) / len(cleaned) if cleaned else None


def deload_check(db: Database) -> dict[str, Any] | None:
    """Evidence dict when a deload week is due, else None. Caller owns cooldown."""
    weekly = _weekly_loads(db)
    rising = all(weekly[i] > weekly[i + 1] for i in range(DELOAD_WEEKS - 1)) and weekly[-1] > 0
    if not rising:
        return None

    metrics = sorted(db.get_recent_metrics(days=28), key=lambda row: row["date"])
    hrv_recent = _mean([row.get("hrv_last_night") for row in metrics[-7:]])
    hrv_baseline = _mean([row.get("hrv_last_night") for row in metrics])
    readiness_recent = _mean([row.get("training_readiness_score") for row in metrics[-7:]])
    readiness_baseline = _mean([row.get("training_readiness_score") for row in metrics])

    hrv_degraded = (
        hrv_recent is not None and hrv_baseline is not None
        and hrv_recent <= DELOAD_HRV_RATIO * hrv_baseline
    )
    readiness_degraded = (
        readiness_recent is not None and readiness_baseline is not None
        and readiness_recent <= readiness_baseline - DELOAD_READINESS_DROP
    )
    if not (hrv_degraded or readiness_degraded):
        return None

    return {
        "weekly_loads": weekly,
        "hrv_recent": round(hrv_recent, 1) if hrv_recent is not None else None,
        "hrv_baseline": round(hrv_baseline, 1) if hrv_baseline is not None else None,
        "readiness_recent": round(readiness_recent, 1) if readiness_recent is not None else None,
        "readiness_baseline": round(readiness_baseline, 1) if readiness_baseline is not None else None,
    }
