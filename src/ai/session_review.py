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


def _typical_comparison(db: Database, activity: dict[str, Any]) -> str:
    """Load vs the 90-day mean for this activity type."""
    same_type = [
        a for a in db.get_recent_activities(days=90, activity_type=str(activity.get("type")))
        if a["id"] != activity["id"] and (a.get("training_load") or 0) > 0
    ]
    if len(same_type) < 3:
        return ""
    mean_load = sum(a["training_load"] for a in same_type) / len(same_type)
    load = activity.get("training_load") or 0
    if mean_load <= 0 or load <= 0:
        return ""
    ratio = load / mean_load
    return (
        f"Load {load:.0f} = {ratio * 100:.0f}% of your typical"
        f" {activity.get('type')} session (n={len(same_type)})"
    )


def _strength_details(db: Database, activity: dict[str, Any]) -> list[str]:
    from .strength_profile import e1rm
    lines: list[str] = []
    prior_best: dict[str, float] = {}
    for other in db.get_recent_activities(days=365, activity_type="strength"):
        if other["id"] == activity["id"]:
            continue
        for set_row in db.get_gym_sets(other["id"]):
            weight, reps = set_row.get("weight_lb"), set_row.get("reps")
            exercise = str(set_row.get("exercise") or "")
            if weight and reps and exercise:
                prior_best[exercise] = max(prior_best.get(exercise, 0.0), e1rm(weight, reps))

    session_sets: dict[str, list[str]] = {}
    session_best: dict[str, float] = {}
    for set_row in db.get_gym_sets(activity["id"]):
        exercise = str(set_row.get("exercise") or "")
        if not exercise:
            continue
        weight, reps = set_row.get("weight_lb"), set_row.get("reps")
        session_sets.setdefault(exercise, []).append(
            f"{reps}x{weight:.0f}lb" if weight else f"{reps} reps"
        )
        if weight and reps:
            session_best[exercise] = max(session_best.get(exercise, 0.0), e1rm(weight, reps))

    for exercise, sets in session_sets.items():
        pr_marker = ""
        if exercise in session_best and session_best[exercise] > prior_best.get(exercise, 0.0) > 0:
            pr_marker = "  <-- PR (new best e1RM)"
        lines.append(f"- {exercise}: {', '.join(sets)}{pr_marker}")
    return lines


def _basketball_details(db: Database, activity: dict[str, Any]) -> list[str]:
    from pathlib import Path
    from .basketball_profile import hr_drift_pct, zone45_share
    lines: list[str] = []
    fit_path = activity.get("fit_file_path")
    if fit_path and Path(str(fit_path)).exists():
        try:
            from ..garmin.fit_parser import parse_hr_series
            drift = hr_drift_pct(parse_hr_series(fit_path))
            if drift is not None:
                lines.append(f"- HR drift 2nd half vs 1st: {drift:+.1f}% (conditioning fade proxy)")
        except Exception:
            pass
    shares = zone45_share(db, days=90)
    this_one = [s for s in shares if s["date"] == str(activity.get("date"))]
    if this_one:
        lines.append(f"- Zone 4-5 share: {this_one[-1]['share']:.0%}")
    return lines


def _ski_details(db: Database, activity: dict[str, Any]) -> list[str]:
    runs = db.get_ski_runs(activity["id"])
    if not runs:
        return []
    speeds = [run.get("avg_speed_kmh") for run in runs if run.get("avg_speed_kmh")]
    lines = [f"- {len(runs)} runs"]
    if speeds:
        lines.append(
            f"- avg speed {sum(speeds) / len(speeds):.1f} km/h,"
            f" fastest run #{speeds.index(max(speeds)) + 1}"
        )
    return lines


def _needs_feedback(db: Database, activity: dict[str, Any]) -> bool:
    return db.get_training_feedback(activity["id"]) is None


def review_block(db: Database, activity: dict[str, Any]) -> str:
    """One computed review block. The LLM presents; it never recomputes."""
    activity_type = str(activity.get("type") or "")
    lines = [
        f"## Session Review — {activity.get('date')} {activity_type}"
        f" ({activity.get('duration_min', 0):.0f} min, load {activity.get('training_load') or 0:.0f})",
    ]
    comparison = _typical_comparison(db, activity)
    if comparison:
        lines.append(comparison)

    if activity_type == "strength":
        lines.extend(_strength_details(db, activity))
    elif activity_type == "basketball":
        lines.extend(_basketball_details(db, activity))
    elif activity_type == "skiing":
        lines.extend(_ski_details(db, activity))

    lines.append(f"ASK_FEEDBACK: {'yes' if _needs_feedback(db, activity) else 'no'}")
    return "\n".join(lines)


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
