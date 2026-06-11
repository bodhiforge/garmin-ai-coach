"""Strength intelligence — pure Python calculators over gym set history.

Python computes every number. The LLM only presents gated findings.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from ..db.models import Database

EXCLUDED_EXERCISES = {"Treadmill"}
EXCLUDED_PREFIXES = ("Stretch",)

EXERCISE_MUSCLE_MAP: dict[str, tuple[str, ...]] = {
    "Romanian Deadlift": ("hamstrings", "glutes"),
    "Seated Cable Row": ("back",),
    "Lat Pulldown": ("back",),
    "Straight Arm Pulldown": ("back",),
    "Face Pull": ("rear_delts",),
    "Lateral Raise": ("side_delts",),
    "Cable Crossover": ("chest",),
    "Shoulder Press": ("front_delts",),
    "Barbell Hip Thrust On Floor": ("glutes",),
    "Weighted Hip Raise": ("glutes",),
    "Hip Raise": ("glutes",),
    "Weighted Standing Hip Abduction": ("glute_med",),
    "Weighted Sliding Hip Adduction": ("adductors",),
    "Weighted Leg Curl": ("hamstrings",),
    "Leg Curl": ("hamstrings",),
    "Dumbbell Bulgarian Split Squat": ("quads", "glutes"),
    "Overhead Bulgarian Split Squat": ("quads", "glutes"),
    "Leg Press": ("quads", "glutes"),
    "Cable Woodchop": ("core",),
    "Weighted Sit Up": ("core",),
    "Leg Raise": ("core",),
}

EXERCISE_PATTERN_MAP: dict[str, str] = {
    "Romanian Deadlift": "hinge",
    "Barbell Hip Thrust On Floor": "hinge",
    "Weighted Hip Raise": "hinge",
    "Hip Raise": "hinge",
    "Dumbbell Bulgarian Split Squat": "lunge",
    "Overhead Bulgarian Split Squat": "lunge",
    "Leg Press": "squat",
    "Seated Cable Row": "pull_h",
    "Face Pull": "pull_h",
    "Lat Pulldown": "pull_v",
    "Straight Arm Pulldown": "pull_v",
    "Cable Crossover": "push_h",
    "Shoulder Press": "push_v",
    "Cable Woodchop": "core",
    "Weighted Sit Up": "core",
    "Leg Raise": "core",
}

CORE_PATTERNS = ("squat", "hinge", "lunge", "push_h", "push_v", "pull_h", "pull_v", "core")

MAJOR_MUSCLE_GROUPS = ("back", "chest", "quads", "hamstrings", "glutes")
WEEKLY_SET_FLOOR = 10   # below ⇒ likely under-stimulating (MEV reference)
WEEKLY_SET_CEILING = 20  # above ⇒ likely junk volume / recovery debt (MAV reference)

STRENGTH_REP_MAX = 6
HYPERTROPHY_REP_MAX = 12


def _is_lift(exercise: str) -> bool:
    if exercise == "" or exercise in EXCLUDED_EXERCISES:
        return False
    return not exercise.startswith(EXCLUDED_PREFIXES)


def weekly_muscle_volume(db: Database, days: int = 28) -> dict[str, dict[str, Any]]:
    """Average weekly working sets per major muscle group vs volume landmarks."""
    weeks = max(days / 7, 1)
    set_counts: dict[str, int] = defaultdict(int)
    for row in load_strength_sets(db, days=days):
        for muscle in EXERCISE_MUSCLE_MAP.get(row["exercise"], ()):
            set_counts[muscle] += 1

    volume: dict[str, dict[str, Any]] = {}
    for muscle in MAJOR_MUSCLE_GROUPS:
        weekly_sets = round(set_counts.get(muscle, 0) / weeks, 1)
        flag = "ok"
        if weekly_sets < WEEKLY_SET_FLOOR:
            flag = "below_floor"
        elif weekly_sets > WEEKLY_SET_CEILING:
            flag = "above_ceiling"
        volume[muscle] = {"weekly_sets": weekly_sets, "flag": flag}
    return volume


def movement_pattern_matrix(db: Database, days: int = 90) -> dict[str, Any]:
    """Set counts per movement pattern + list of uncovered core patterns."""
    counts: dict[str, int] = {pattern: 0 for pattern in CORE_PATTERNS}
    unmapped: set[str] = set()
    for row in load_strength_sets(db, days=days):
        pattern = EXERCISE_PATTERN_MAP.get(row["exercise"])
        if pattern is None:
            unmapped.add(row["exercise"])
            continue
        counts[pattern] += 1
    gaps = [pattern for pattern in CORE_PATTERNS if counts[pattern] == 0]
    return {"counts": counts, "gaps": gaps, "unmapped": sorted(unmapped)}


PLATEAU_MIN_SESSIONS = 4
PLATEAU_BAND = 0.025  # last 3 session-bests within ±2.5% ⇒ flat


def e1rm(weight_lb: float, reps: int) -> float:
    """Epley estimated 1-rep max."""
    if reps <= 1:
        return weight_lb
    return weight_lb * (1 + reps / 30)


def e1rm_trend(db: Database, days: int = 90) -> dict[str, dict[str, Any]]:
    """Per exercise: session-best e1RM series (date ascending) + plateau flag."""
    session_best: dict[str, dict[str, float]] = defaultdict(dict)
    for row in load_strength_sets(db, days=days):
        if row["weight_lb"] is None or row["weight_lb"] <= 0 or not row["reps"]:
            continue
        estimate = e1rm(row["weight_lb"], row["reps"])
        day = str(row["date"])
        best = session_best[row["exercise"]]
        best[day] = max(best.get(day, 0.0), estimate)

    trend: dict[str, dict[str, Any]] = {}
    for exercise, by_day in session_best.items():
        series = [round(by_day[day], 1) for day in sorted(by_day)]
        recent = series[-3:]
        is_flat = (
            len(series) >= PLATEAU_MIN_SESSIONS
            and len(recent) == 3
            and (max(recent) - min(recent)) <= PLATEAU_BAND * max(recent)
            and max(recent) <= max(series[:-3] + [recent[0]])
        )
        trend[exercise] = {
            "series": series,
            "latest_e1rm": series[-1],
            "best_e1rm": max(series),
            "sessions": len(series),
            "plateau": is_flat,
        }
    return trend


def load_strength_sets(db: Database, days: int = 90) -> list[dict[str, Any]]:
    """Flat list of lift sets across recent strength sessions, newest first.
    Each row: exercise, reps, weight_lb, rest_duration_sec, date, activity_id.
    get_gym_sets already merges manual_gym_sets rows."""
    activities = db.get_recent_activities(days=days, activity_type="strength")
    rows: list[dict[str, Any]] = []
    for activity in activities:
        for set_row in db.get_gym_sets(activity["id"]):
            exercise = str(set_row.get("exercise") or "").strip()
            if not _is_lift(exercise):
                continue
            rows.append({
                "exercise": exercise,
                "reps": set_row.get("reps"),
                "weight_lb": set_row.get("weight_lb"),
                "rest_duration_sec": set_row.get("rest_duration_sec"),
                "date": activity.get("date"),
                "activity_id": activity["id"],
            })
    return rows
