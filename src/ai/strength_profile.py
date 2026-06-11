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
