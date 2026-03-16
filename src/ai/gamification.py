"""Gamification — achievements, streaks, and challenges. Pure Python."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..db.models import Database

logger = logging.getLogger(__name__)


# --- Achievements ---

ACHIEVEMENT_DEFS = [
    # (id, name, check_fn_name, description)
    ("first_blood", "First Blood", "_check_first_activity", "Complete your first tracked activity"),
    ("ski_10", "Ski Rat", "_check_ski_sessions", "Complete 10 ski sessions"),
    ("ski_25", "Snow Addict", "_check_ski_25", "Complete 25 ski sessions"),
    ("gym_10", "Iron Regular", "_check_gym_sessions", "Complete 10 gym sessions"),
    ("gym_25", "Gym Rat", "_check_gym_25", "Complete 25 gym sessions"),
    ("speed_30", "Speed Demon 30", "_check_speed_30", "Hit 30 km/h on a ski run"),
    ("speed_40", "Speed Demon 40", "_check_speed_40", "Hit 40 km/h on a ski run"),
    ("speed_50", "Speed Demon 50", "_check_speed_50", "Hit 50 km/h on a ski run"),
    ("volume_1000", "Ton Lifter", "_check_volume_1000", "Lift 1,000 kg total volume in one session"),
    ("volume_5000", "Iron Mountain", "_check_volume_5000", "Lift 5,000 kg total volume in one session"),
    ("streak_3", "Hat Trick", "_check_streak_3", "Train 3 days in a row"),
    ("streak_7", "Week Warrior", "_check_streak_7", "Train 7 days in a row"),
    ("early_bird_7", "Early Bird", "_check_early_bird", "Sleep before midnight 7 nights in a row"),
    ("comeback", "Comeback Kid", "_check_comeback", "Return to training after 7+ days off"),
]


def check_achievements(db: Database, data_dir: Path) -> list[dict[str, str]]:
    """Check for newly unlocked achievements. Returns list of {id, name, description}."""
    tracker_path = data_dir / "achievements.json"
    unlocked = _load_tracker(tracker_path)

    newly_unlocked = []
    for ach_id, name, check_fn_name, description in ACHIEVEMENT_DEFS:
        if ach_id in unlocked:
            continue
        check_fn = globals().get(check_fn_name)
        if check_fn and check_fn(db):
            unlocked[ach_id] = str(date.today())
            newly_unlocked.append({"id": ach_id, "name": name, "description": description})
            logger.info("Achievement unlocked: %s", name)

    if newly_unlocked:
        _save_tracker(tracker_path, unlocked)

    return newly_unlocked


def get_all_achievements(db: Database, data_dir: Path) -> tuple[list[dict], list[dict]]:
    """Returns (unlocked, locked) achievement lists."""
    tracker_path = data_dir / "achievements.json"
    unlocked_ids = _load_tracker(tracker_path)

    unlocked = []
    locked = []
    for ach_id, name, _, description in ACHIEVEMENT_DEFS:
        entry = {"id": ach_id, "name": name, "description": description}
        if ach_id in unlocked_ids:
            entry["date"] = unlocked_ids[ach_id]
            unlocked.append(entry)
        else:
            locked.append(entry)

    return unlocked, locked


def _load_tracker(path: Path) -> dict[str, str]:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_tracker(path: Path, data: dict[str, str]) -> None:
    path.write_text(json.dumps(data, indent=2))


# --- Achievement check functions ---

def _check_first_activity(db: Database) -> bool:
    return len(db.get_recent_activities(days=9999)) >= 1


def _check_ski_sessions(db: Database) -> bool:
    return len(db.get_recent_activities(days=9999, activity_type="skiing")) >= 10


def _check_ski_25(db: Database) -> bool:
    return len(db.get_recent_activities(days=9999, activity_type="skiing")) >= 25


def _check_gym_sessions(db: Database) -> bool:
    return len(db.get_recent_activities(days=9999, activity_type="strength")) >= 10


def _check_gym_25(db: Database) -> bool:
    return len(db.get_recent_activities(days=9999, activity_type="strength")) >= 25


def _check_speed_30(db: Database) -> bool:
    return _max_ski_speed(db) >= 30


def _check_speed_40(db: Database) -> bool:
    return _max_ski_speed(db) >= 40


def _check_speed_50(db: Database) -> bool:
    return _max_ski_speed(db) >= 50


def _max_ski_speed(db: Database) -> float:
    activities = db.get_recent_activities(days=9999, activity_type="skiing")
    max_speed = 0
    for a in activities:
        runs = db.get_ski_runs(a["id"])
        for r in (runs or []):
            speed = r.get("max_speed_kmh", 0) or 0
            if speed > max_speed:
                max_speed = speed
    return max_speed


def _check_volume_1000(db: Database) -> bool:
    return _max_session_volume(db) >= 1000


def _check_volume_5000(db: Database) -> bool:
    return _max_session_volume(db) >= 5000


def _max_session_volume(db: Database) -> float:
    activities = db.get_recent_activities(days=9999, activity_type="strength")
    max_vol = 0
    for a in activities:
        sets = db.get_gym_sets(a["id"])
        vol = sum(
            (s.get("weight_kg", 0) or 0) * (s.get("reps", 0) or 0)
            for s in (sets or [])
        )
        if vol > max_vol:
            max_vol = vol
    return max_vol


def _check_streak_3(db: Database) -> bool:
    return _max_training_streak(db) >= 3


def _check_streak_7(db: Database) -> bool:
    return _max_training_streak(db) >= 7


def _max_training_streak(db: Database) -> int:
    activities = db.get_recent_activities(days=365)
    if not activities:
        return 0
    activity_dates = sorted(set(a["date"] for a in activities))
    max_streak = 1
    current = 1
    for i in range(1, len(activity_dates)):
        prev = date.fromisoformat(activity_dates[i - 1])
        curr = date.fromisoformat(activity_dates[i])
        if (curr - prev).days == 1:
            current += 1
            if current > max_streak:
                max_streak = current
        else:
            current = 1
    return max_streak


def _check_early_bird(db: Database) -> bool:
    # Needs sleep_start_time which may not be in daily_metrics
    # Approximate: 7 consecutive days with sleep_duration >= 7h
    metrics = db.get_recent_metrics(days=30)
    if len(metrics) < 7:
        return False
    streak = 0
    for m in metrics:
        sleep = m.get("sleep_duration_min", 0) or 0
        if sleep >= 420:  # 7h+
            streak += 1
            if streak >= 7:
                return True
        else:
            streak = 0
    return False


def _check_comeback(db: Database) -> bool:
    activities = db.get_recent_activities(days=365)
    if len(activities) < 2:
        return False
    dates = sorted(set(a["date"] for a in activities))
    for i in range(1, len(dates)):
        gap = (date.fromisoformat(dates[i]) - date.fromisoformat(dates[i - 1])).days
        if gap >= 7:
            return True
    return False


# --- Streaks ---

def current_streaks(db: Database) -> dict[str, Any]:
    """Compute current active streaks."""
    activities = db.get_recent_activities(days=90)
    metrics = db.get_recent_metrics(days=30)

    result = {}

    # Training streak
    activity_dates = sorted(set(a["date"] for a in activities), reverse=True)
    training_streak = 0
    check = date.today()
    for _ in range(90):
        if str(check) in activity_dates:
            training_streak += 1
        elif str(check - timedelta(days=1)) == str(check):
            break
        else:
            break
        check -= timedelta(days=1)
    result["training_streak"] = training_streak

    # Sleep streak (7h+)
    sleep_streak = 0
    for m in metrics:
        sleep = m.get("sleep_duration_min", 0) or 0
        if sleep >= 420:
            sleep_streak += 1
        else:
            break
    result["sleep_streak"] = sleep_streak

    # Ski streak (consecutive ski days)
    ski_dates = sorted(
        set(a["date"] for a in activities if a["type"] == "skiing"),
        reverse=True,
    )
    ski_streak = 0
    check = date.today()
    for _ in range(30):
        if str(check) in ski_dates:
            ski_streak += 1
        else:
            break
        check -= timedelta(days=1)
    result["ski_streak"] = ski_streak

    return result


# --- Challenges ---

def generate_challenge(db: Database) -> dict[str, str] | None:
    """Generate a weekly challenge based on current level. Returns {title, description, metric} or None."""
    # Ski challenge
    ski_activities = db.get_recent_activities(days=30, activity_type="skiing")
    if ski_activities:
        # Speed challenge: current max + small stretch
        max_speed = 0
        for a in ski_activities:
            runs = db.get_ski_runs(a["id"])
            for r in (runs or []):
                speed = r.get("max_speed_kmh", 0) or 0
                if speed > max_speed:
                    max_speed = speed
        if max_speed > 0:
            target = round(max_speed + 1.5, 1)
            return {
                "title": f"Speed Challenge: {target} km/h",
                "description": f"Hit {target} km/h on a single run (current best: {max_speed:.1f})",
                "metric": "ski_max_speed",
            }

    # Gym challenge
    gym_activities = db.get_recent_activities(days=30, activity_type="strength")
    if gym_activities:
        # Volume challenge: beat last session's total volume
        latest = gym_activities[0]
        sets = db.get_gym_sets(latest["id"])
        if sets:
            current_vol = sum(
                (s.get("weight_kg", 0) or 0) * (s.get("reps", 0) or 0)
                for s in sets
            )
            target_vol = round(current_vol * 1.05)
            return {
                "title": f"Volume Challenge: {target_vol} kg",
                "description": f"Hit {target_vol} kg total volume in one session (last: {current_vol:.0f})",
                "metric": "gym_total_volume",
            }

    # Training frequency challenge
    activities = db.get_recent_activities(days=14)
    freq = len(activities) / 2  # per week
    target_freq = round(freq + 0.5)
    return {
        "title": f"Consistency Challenge: {target_freq}x this week",
        "description": f"Train {target_freq} times this week (recent avg: {freq:.1f}/week)",
        "metric": "weekly_sessions",
    }


def format_achievements_text(db: Database, data_dir: Path) -> str:
    """Format achievements + streaks + challenge as text."""
    unlocked, locked = get_all_achievements(db, data_dir)
    streaks = current_streaks(db)
    challenge = generate_challenge(db)

    lines = ["Achievements"]
    if unlocked:
        for a in unlocked:
            lines.append(f"  ✅ {a['name']} — {a['description']} ({a.get('date', '?')})")
    else:
        lines.append("  None yet — keep training!")

    lines.append(f"\nStreaks")
    if streaks.get("training_streak", 0) > 0:
        lines.append(f"  🔥 Training: {streaks['training_streak']} days")
    if streaks.get("sleep_streak", 0) > 0:
        lines.append(f"  😴 Sleep 7h+: {streaks['sleep_streak']} nights")
    if streaks.get("ski_streak", 0) > 0:
        lines.append(f"  ⛷️ Skiing: {streaks['ski_streak']} days")
    if all(v == 0 for v in streaks.values()):
        lines.append("  No active streaks")

    if challenge:
        lines.append(f"\nThis Week's Challenge")
        lines.append(f"  🎯 {challenge['title']}")
        lines.append(f"     {challenge['description']}")

    locked_count = len(locked)
    if locked_count > 0:
        lines.append(f"\n{locked_count} achievements remaining")

    return "\n".join(lines)
