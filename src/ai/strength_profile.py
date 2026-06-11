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


COMPOUND_PATTERNS = ("squat", "hinge", "lunge", "push_h", "push_v", "pull_h", "pull_v")
COMPOUND_REST_FLOOR_SEC = 60


def rep_zone_distribution(db: Database, days: int = 90) -> dict[str, Any]:
    """Share of working sets per rep zone: strength ≤6, hypertrophy 7-12, endurance 13+."""
    strength_sets = hypertrophy_sets = endurance_sets = 0
    for row in load_strength_sets(db, days=days):
        reps = row["reps"]
        if not reps or reps <= 0:
            continue
        if reps <= STRENGTH_REP_MAX:
            strength_sets += 1
        elif reps <= HYPERTROPHY_REP_MAX:
            hypertrophy_sets += 1
        else:
            endurance_sets += 1
    total = strength_sets + hypertrophy_sets + endurance_sets
    if total == 0:
        return {"total_sets": 0, "strength_pct": 0.0, "hypertrophy_pct": 0.0, "endurance_pct": 0.0}
    return {
        "total_sets": total,
        "strength_pct": round(100 * strength_sets / total, 1),
        "hypertrophy_pct": round(100 * hypertrophy_sets / total, 1),
        "endurance_pct": round(100 * endurance_sets / total, 1),
    }


def rest_interval_analysis(db: Database, days: int = 90) -> dict[str, Any]:
    """Median rest on compound patterns; flags chronically rushed rests."""
    compound_rests = [
        row["rest_duration_sec"]
        for row in load_strength_sets(db, days=days)
        if row["rest_duration_sec"] is not None
        and EXERCISE_PATTERN_MAP.get(row["exercise"]) in COMPOUND_PATTERNS
    ]
    if not compound_rests:
        return {"compound_median_sec": None, "rushed_compounds": False, "sample": 0}
    median_rest = median(compound_rests)
    return {
        "compound_median_sec": round(median_rest, 1),
        "rushed_compounds": median_rest < COMPOUND_REST_FLOOR_SEC,
        "sample": len(compound_rests),
    }


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


FINDING_MIN_SESSIONS = 10
FINDING_MIN_SETS = 80
PULL_PUSH_RATIO_GATE = 2.0


def strength_structural_findings(db: Database, days: int = 90) -> list[dict[str, Any]]:
    """Gated structural findings ready for the insights store. Empty on thin data."""
    rows = load_strength_sets(db, days=days)
    sessions = len({row["activity_id"] for row in rows})
    if sessions < FINDING_MIN_SESSIONS or len(rows) < FINDING_MIN_SETS:
        return []

    findings: list[dict[str, Any]] = []
    matrix = movement_pattern_matrix(db, days=days)
    counts = matrix["counts"]

    pull_sets = counts["pull_h"] + counts["pull_v"]
    push_sets = counts["push_h"] + counts["push_v"]
    if push_sets > 0 and pull_sets / push_sets >= PULL_PUSH_RATIO_GATE:
        ratio = round(pull_sets / push_sets, 1)
        findings.append({
            "key": "strength.pull_push_imbalance",
            "statement": (
                f"Pull volume is {ratio}x push volume over the last {days} days"
                f" ({pull_sets} vs {push_sets} sets). Deliberate posture bias or drift?"
            ),
            "evidence": {"pull_sets": pull_sets, "push_sets": push_sets,
                         "ratio": ratio, "sessions": sessions, "window_days": days},
        })

    if counts["squat"] == 0:
        findings.append({
            "key": "strength.no_squat_pattern",
            "statement": (
                f"Zero squat-pattern sets across {sessions} sessions in {days} days —"
                " all knee-dominant work is lunge variants. Worth a deliberate decision."
            ),
            "evidence": {"sessions": sessions, "lunge_sets": counts["lunge"], "window_days": days},
        })

    zones = rep_zone_distribution(db, days=days)
    if zones["total_sets"] >= FINDING_MIN_SETS and zones["strength_pct"] == 0.0:
        findings.append({
            "key": "strength.no_strength_zone_work",
            "statement": (
                f"0% of {zones['total_sets']} sets in the ≤{STRENGTH_REP_MAX}-rep strength zone"
                f" ({days}d) — everything lives at {zones['hypertrophy_pct']}% hypertrophy /"
                f" {zones['endurance_pct']}% endurance reps."
            ),
            "evidence": {**zones, "window_days": days},
        })

    rest = rest_interval_analysis(db, days=days)
    if rest["sample"] >= FINDING_MIN_SETS and rest["rushed_compounds"]:
        findings.append({
            "key": "strength.rushed_compound_rests",
            "statement": (
                f"Median rest on compound lifts is {rest['compound_median_sec']}s"
                f" (n={rest['sample']}) — under {COMPOUND_REST_FLOOR_SEC}s, which caps load progression."
            ),
            "evidence": {**rest, "window_days": days},
        })

    return findings


def store_strength_findings(db: Database) -> int:
    """Persist gated findings; returns number of new rows."""
    inserted = 0
    for finding in strength_structural_findings(db):
        if db.insert_insight(
            key=finding["key"],
            category="strength",
            statement=finding["statement"],
            evidence=finding["evidence"],
        ):
            inserted += 1
    return inserted


def strength_profile_block(db: Database, days: int = 90) -> str:
    """Formatted profile for digests and on-demand queries — same register as
    the existing computed layers in insights.py."""
    lines = ["## Strength Profile (computed — LLM MUST use this)"]

    trend = e1rm_trend(db, days=days)
    for exercise in sorted(trend, key=lambda name: -trend[name]["sessions"])[:8]:
        info = trend[exercise]
        marker = " — PLATEAU" if info["plateau"] else ""
        lines.append(
            f"- {exercise}: e1RM {info['latest_e1rm']}lb (best {info['best_e1rm']}lb,"
            f" {info['sessions']} sessions){marker}"
        )

    volume = weekly_muscle_volume(db)
    volume_parts = [
        f"{muscle} {data['weekly_sets']}/wk"
        + (" LOW" if data["flag"] == "below_floor" else " HIGH" if data["flag"] == "above_ceiling" else "")
        for muscle, data in volume.items()
    ]
    lines.append("Weekly sets vs landmarks (28d): " + ", ".join(volume_parts))

    matrix = movement_pattern_matrix(db, days=days)
    lines.append(
        "Pattern coverage: "
        + ", ".join(f"{pattern} {count}" for pattern, count in matrix["counts"].items())
        + (f" | gaps: {', '.join(matrix['gaps'])}" if matrix["gaps"] else "")
    )

    zones = rep_zone_distribution(db, days=days)
    lines.append(
        f"Rep zones ({zones['total_sets']} sets): strength {zones['strength_pct']}%,"
        f" hypertrophy {zones['hypertrophy_pct']}%, endurance {zones['endurance_pct']}%"
    )

    rest = rest_interval_analysis(db, days=days)
    if rest["compound_median_sec"] is not None:
        lines.append(
            f"Compound rest median: {rest['compound_median_sec']}s (n={rest['sample']})"
            + (" — RUSHED" if rest["rushed_compounds"] else "")
        )

    return "\n".join(lines)


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
