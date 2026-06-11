"""Basketball-specific conditioning analysis: in-session HR drift, high-zone
share trend. The day-after recovery cost lives in discovery.py."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db.models import Database

DRIFT_MIN_DURATION_SEC = 20 * 60
DRIFT_TRIM_FRACTION = 0.1  # ignore first/last 10% (warm-up, cooldown)


def hr_drift_pct(series: list[tuple[float, int]]) -> float | None:
    """Second-half vs first-half mean HR, % — a conditioning-fade proxy."""
    if not series or series[-1][0] < DRIFT_MIN_DURATION_SEC:
        return None
    total = series[-1][0]
    trimmed = [
        (elapsed, heart_rate) for elapsed, heart_rate in series
        if DRIFT_TRIM_FRACTION * total <= elapsed <= (1 - DRIFT_TRIM_FRACTION) * total
    ]
    if len(trimmed) < 10:
        return None
    midpoint = (trimmed[0][0] + trimmed[-1][0]) / 2
    first = [heart_rate for elapsed, heart_rate in trimmed if elapsed <= midpoint]
    second = [heart_rate for elapsed, heart_rate in trimmed if elapsed > midpoint]
    if not first or not second:
        return None
    first_mean = sum(first) / len(first)
    if first_mean == 0:
        return None
    return round(100 * (sum(second) / len(second) - first_mean) / first_mean, 1)


def zone45_share(db: Database, days: int = 90) -> list[dict[str, Any]]:
    """Per-session share of time in HR zones 4-5, date ascending."""
    shares: list[dict[str, Any]] = []
    for activity in db.get_recent_activities(days=days, activity_type="basketball"):
        zone_seconds = [activity.get(f"hr_zone{zone}_sec") or 0 for zone in range(1, 6)]
        total = sum(zone_seconds)
        if total == 0:
            continue
        shares.append({
            "date": str(activity["date"]),
            "share": round((zone_seconds[3] + zone_seconds[4]) / total, 2),
        })
    return sorted(shares, key=lambda row: row["date"])


def basketball_profile_block(db: Database, days: int = 90) -> str:
    """Formatted block — same register as the strength profile."""
    from ..garmin.fit_parser import parse_hr_series

    lines = ["## Basketball Profile (computed — LLM MUST use this)"]
    drifts: list[tuple[str, float]] = []
    for activity in db.get_recent_activities(days=days, activity_type="basketball"):
        fit_path = activity.get("fit_file_path")
        if not fit_path or not Path(fit_path).exists():
            continue
        try:
            drift = hr_drift_pct(parse_hr_series(fit_path))
        except Exception:
            continue
        if drift is not None:
            drifts.append((str(activity["date"]), drift))
    if drifts:
        drifts.sort()
        recent = ", ".join(f"{day}: {value:+.1f}%" for day, value in drifts[-5:])
        lines.append(f"HR drift (2nd half vs 1st, last {min(len(drifts), 5)} sessions): {recent}")

    shares = zone45_share(db, days=days)
    if shares:
        recent_shares = ", ".join(f"{row['date']}: {row['share']:.0%}" for row in shares[-5:])
        lines.append(f"Zone 4-5 share per session: {recent_shares}")

    if len(lines) == 1:
        lines.append("No basketball sessions with usable HR data in the window.")
    return "\n".join(lines)
