"""Data-driven behavioral observations. Python detects patterns, saves to memory."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..db.models import Database

logger = logging.getLogger(__name__)


def detect_observations(db: Database, memory_dir: Path) -> list[str]:
    """Detect behavioral patterns from data. Returns list of new observations found.
    Saves to observations.md in memory_dir. Pure Python, no LLM."""

    observations: list[str] = []

    observations.extend(_ski_fatigue_pattern(db))
    observations.extend(_training_schedule_pattern(db))
    observations.extend(_rest_compliance(db))
    observations.extend(_recovery_by_activity_type(db))
    observations.extend(_sleep_training_correlation(db))
    observations.extend(_consecutive_day_impact(db))

    if not observations:
        return []

    # Load existing observations to avoid duplicates
    obs_path = memory_dir / "observations.md"
    existing = obs_path.read_text() if obs_path.exists() else ""

    new_observations = []
    for obs in observations:
        # Check if this observation (or very similar) already exists
        obs_key = obs.split(":")[0].strip() if ":" in obs else obs[:40]
        if obs_key not in existing:
            new_observations.append(obs)

    if not new_observations:
        return []

    # Append new observations with date
    today = str(date.today())
    lines = [existing.rstrip()] if existing.strip() else ["# Coach Observations", ""]
    lines.append(f"\n## {today}")
    for obs in new_observations:
        lines.append(f"- {obs}")

    obs_path.write_text("\n".join(lines) + "\n")
    logger.info("Saved %d new observations to observations.md", len(new_observations))

    return new_observations


def _ski_fatigue_pattern(db: Database) -> list[str]:
    """Detect consistent fatigue run number across ski sessions."""
    activities = db.get_recent_activities(days=365, activity_type="skiing")
    if len(activities) < 3:
        return []

    decline_runs = []
    for a in activities:
        runs = db.get_ski_runs(a["id"])
        if not runs or len(runs) < 3:
            continue
        speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
        peak = max(speeds)
        for i, s in enumerate(speeds):
            if i > 0 and s < peak * 0.85:
                decline_runs.append(i + 1)
                break

    if len(decline_runs) < 3:
        return []

    avg_decline = sum(decline_runs) / len(decline_runs)
    # Check consistency — are they clustered?
    within_1 = sum(1 for d in decline_runs if abs(d - avg_decline) <= 1)
    if within_1 / len(decline_runs) >= 0.6:
        return [f"Ski fatigue pattern: speed consistently drops after run {avg_decline:.0f} "
                f"(seen in {len(decline_runs)}/{len(activities)} sessions)"]
    return []


def _training_schedule_pattern(db: Database) -> list[str]:
    """Detect which days user trains and which they skip."""
    activities = db.get_recent_activities(days=90)
    if len(activities) < 10:
        return []

    day_counts = [0] * 7  # Mon=0 ... Sun=6
    for a in activities:
        d = date.fromisoformat(a["date"])
        day_counts[d.weekday()] += 1

    total_weeks = 90 / 7
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    results = []
    # Find days they almost never train
    never_days = [day_names[i] for i in range(7) if day_counts[i] / total_weeks < 0.1]
    if never_days:
        results.append(f"Training schedule: almost never trains on {', '.join(never_days)}")

    # Find favorite training days
    fav_days = [day_names[i] for i in range(7) if day_counts[i] / total_weeks > 0.5]
    if fav_days:
        results.append(f"Training schedule: most active on {', '.join(fav_days)}")

    return results


def _rest_compliance(db: Database) -> list[str]:
    """Check if user follows rest advice on low-readiness days."""
    metrics = db.get_recent_metrics(days=60)
    activities = db.get_recent_activities(days=60)
    notifications = db.get_notifications_since(str(date.today() - timedelta(days=60)))

    if len(metrics) < 7:
        return []

    # Find low-readiness days
    low_days = []
    for m in metrics:
        tr = m.get("training_readiness_score")
        if tr is not None and tr < 40:
            low_days.append(m["date"])

    if len(low_days) < 2:
        return []

    activity_dates = set(a["date"] for a in activities)
    trained_on_low = [(d, d in activity_dates) for d in low_days]
    ignored_count = sum(1 for _, trained in trained_on_low if trained)

    if ignored_count == 0:
        return ["Rest compliance: always rests on low-readiness days — good discipline"]

    # Check consequence: HRV next day after ignoring rest advice
    consequences = []
    for low_date, trained in trained_on_low:
        if not trained:
            continue
        next_date = str(date.fromisoformat(low_date) + timedelta(days=1))
        low_metrics = next(
            (m for m in metrics if m["date"] == low_date), None
        )
        next_metrics = next(
            (m for m in metrics if m["date"] == next_date), None
        )
        if low_metrics and next_metrics:
            hrv_low = low_metrics.get("hrv_last_night")
            hrv_next = next_metrics.get("hrv_last_night")
            if hrv_low and hrv_next:
                change_pct = (hrv_next - hrv_low) / hrv_low * 100
                consequences.append(change_pct)

    results = [
        f"Rest compliance: trained on {ignored_count}/{len(low_days)} low-readiness days"
    ]

    if consequences:
        avg_consequence = sum(consequences) / len(consequences)
        if avg_consequence < -5:
            results.append(
                f"Accountability: ignoring rest advice caused avg {avg_consequence:.0f}% HRV drop next day"
            )

    return results


def _recovery_by_activity_type(db: Database) -> list[str]:
    """Compare HRV recovery speed after different activity types."""
    activities = db.get_recent_activities(days=90)
    metrics = db.get_recent_metrics(days=90)

    if len(activities) < 5 or len(metrics) < 14:
        return []

    metrics_by_date = {m["date"]: m for m in metrics}

    type_recovery: dict[str, list[float]] = {}
    for a in activities:
        act_date = a["date"]
        next_date = str(date.fromisoformat(act_date) + timedelta(days=1))

        hrv_day = metrics_by_date.get(act_date, {}).get("hrv_last_night")
        hrv_next = metrics_by_date.get(next_date, {}).get("hrv_last_night")

        if hrv_day and hrv_next and hrv_day > 0:
            change_pct = (hrv_next - hrv_day) / hrv_day * 100
            act_type = a["type"]
            if act_type not in type_recovery:
                type_recovery[act_type] = []
            type_recovery[act_type].append(change_pct)

    if len(type_recovery) < 2:
        return []

    results = []
    type_avgs = {t: sum(v) / len(v) for t, v in type_recovery.items() if len(v) >= 2}

    if len(type_avgs) >= 2:
        sorted_types = sorted(type_avgs.items(), key=lambda x: x[1], reverse=True)
        best = sorted_types[0]
        worst = sorted_types[-1]
        if best[1] - worst[1] > 5:
            results.append(
                f"Recovery pattern: HRV recovers better after {best[0]} ({best[1]:+.0f}%) "
                f"than {worst[0]} ({worst[1]:+.0f}%)"
            )

    return results


def _sleep_training_correlation(db: Database) -> list[str]:
    """Check if poor sleep leads to worse training performance."""
    activities = db.get_recent_activities(days=90, activity_type="skiing")
    metrics = db.get_recent_metrics(days=90)

    if len(activities) < 4 or len(metrics) < 14:
        return []

    metrics_by_date = {m["date"]: m for m in metrics}

    good_sleep_speeds = []  # >=7h night before
    bad_sleep_speeds = []   # <7h night before

    for a in activities:
        prev_date = str(date.fromisoformat(a["date"]) - timedelta(days=1))
        prev_metrics = metrics_by_date.get(prev_date)
        if not prev_metrics:
            continue
        sleep_min = prev_metrics.get("sleep_duration_min", 0) or 0

        runs = db.get_ski_runs(a["id"])
        if not runs:
            continue
        max_speed = max((r.get("max_speed_kmh", 0) or 0 for r in runs), default=0)
        if max_speed == 0:
            continue

        if sleep_min >= 420:
            good_sleep_speeds.append(max_speed)
        else:
            bad_sleep_speeds.append(max_speed)

    if len(good_sleep_speeds) >= 2 and len(bad_sleep_speeds) >= 2:
        avg_good = sum(good_sleep_speeds) / len(good_sleep_speeds)
        avg_bad = sum(bad_sleep_speeds) / len(bad_sleep_speeds)
        diff_pct = (avg_good - avg_bad) / avg_bad * 100 if avg_bad > 0 else 0
        if diff_pct > 3:
            return [
                f"Sleep-performance link: ski speed averages {avg_good:.1f} km/h after 7h+ sleep "
                f"vs {avg_bad:.1f} km/h after <7h ({diff_pct:+.0f}% difference)"
            ]
    return []


def _consecutive_day_impact(db: Database) -> list[str]:
    """Check if training on consecutive days hurts performance."""
    activities = db.get_recent_activities(days=90, activity_type="skiing")
    if len(activities) < 5:
        return []

    activity_dates = sorted(set(a["date"] for a in activities))
    fresh_speeds = []  # day off before
    consecutive_speeds = []  # trained day before too

    for a in activities:
        prev_date = str(date.fromisoformat(a["date"]) - timedelta(days=1))
        runs = db.get_ski_runs(a["id"])
        if not runs:
            continue
        max_speed = max((r.get("max_speed_kmh", 0) or 0 for r in runs), default=0)
        if max_speed == 0:
            continue

        if prev_date in activity_dates:
            consecutive_speeds.append(max_speed)
        else:
            fresh_speeds.append(max_speed)

    if len(fresh_speeds) >= 2 and len(consecutive_speeds) >= 2:
        avg_fresh = sum(fresh_speeds) / len(fresh_speeds)
        avg_consec = sum(consecutive_speeds) / len(consecutive_speeds)
        diff_pct = (avg_fresh - avg_consec) / avg_consec * 100 if avg_consec > 0 else 0
        if abs(diff_pct) > 3:
            return [
                f"Consecutive day impact: ski speed averages {avg_fresh:.1f} km/h after rest "
                f"vs {avg_consec:.1f} km/h on consecutive days ({diff_pct:+.0f}% difference)"
            ]
    return []
