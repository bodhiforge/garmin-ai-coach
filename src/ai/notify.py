"""Event-driven notification system. Python computes urgency, LLM writes copy."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from ..db.models import Database

logger = logging.getLogger(__name__)

# Urgency rules — each returns (score, event_description)
def _check_new_activity(db: Database) -> tuple[int, str]:
    activities = db.get_recent_activities(days=1)
    if not activities:
        return 0, ""
    latest = activities[0]
    # Check if we already notified about this activity
    hours = db.hours_since_last_notification(f"activity_{latest['id']}")
    if hours < 24:
        return 0, ""
    if latest["type"] == "skiing":
        runs = db.get_ski_runs(latest["id"])
        run_count = len(runs) if runs else 0
        speeds = [r.get("max_speed_kmh", 0) or 0 for r in (runs or [])]
        max_speed = max(speeds) if speeds else 0
        return 3, f"New ski session: {run_count} runs, max {max_speed:.1f} km/h"
    elif latest["type"] == "strength":
        return 1, f"New gym session: {latest.get('duration_min', '?')}min"
    return 0, ""


def _check_ski_pr(db: Database) -> tuple[int, str]:
    activities = db.get_recent_activities(days=365, activity_type="skiing")
    if len(activities) < 2:
        return 0, ""
    
    # Current season max
    season_max = 0
    season_max_date = ""
    for a in activities:
        runs = db.get_ski_runs(a["id"])
        for r in (runs or []):
            speed = r.get("max_speed_kmh", 0) or 0
            if speed > season_max:
                season_max = speed
                season_max_date = a["date"]
    
    # Did we already report this PR?
    hours = db.hours_since_last_notification("ski_pr")
    if hours < 48:
        return 0, ""
    
    # Check if latest session set the PR
    latest = activities[0]
    latest_runs = db.get_ski_runs(latest["id"])
    latest_max = max((r.get("max_speed_kmh", 0) or 0 for r in (latest_runs or [])), default=0)
    
    if latest_max >= season_max and latest_max > 0:
        return 1, f"New season PR: {latest_max:.1f} km/h ({season_max_date})"
    return 0, ""


def _check_hrv_trend(db: Database) -> tuple[int, str]:
    metrics = db.get_recent_metrics(days=5)
    hrvs = [m.get("hrv_last_night") for m in metrics if m.get("hrv_last_night") is not None]
    if len(hrvs) < 3:
        return 0, ""
    
    # Check 3-day declining trend
    recent_3 = hrvs[:3]
    if all(recent_3[i] <= recent_3[i+1] for i in range(len(recent_3)-1)):
        drop_pct = ((recent_3[-1] - recent_3[0]) / recent_3[-1] * 100) if recent_3[-1] > 0 else 0
        if abs(drop_pct) > 10:
            hours = db.hours_since_last_notification("hrv_alert")
            if hours < 24:
                return 0, ""
            return 3, f"HRV declining 3 days ({recent_3[-1]:.0f} -> {recent_3[0]:.0f}ms, {drop_pct:+.0f}%)"
    return 0, ""


def _check_rhr_elevated(db: Database) -> tuple[int, str]:
    metrics = db.get_recent_metrics(days=14)
    rhrs = [m.get("resting_hr") for m in metrics if m.get("resting_hr") is not None]
    if len(rhrs) < 3:
        return 0, ""
    
    avg_rhr = sum(rhrs) / len(rhrs)
    latest_rhr = rhrs[0]
    
    if latest_rhr - avg_rhr > 5:
        hours = db.hours_since_last_notification("rhr_alert")
        if hours < 24:
            return 0, ""
        return 2, f"RHR elevated: {latest_rhr}bpm (avg {avg_rhr:.0f}bpm, +{latest_rhr - avg_rhr:.0f})"
    return 0, ""


def _check_inactive(db: Database) -> tuple[int, str]:
    activities = db.get_recent_activities(days=30)
    if not activities:
        return 2, "No activities in 30 days"
    
    latest_date = activities[0]["date"]
    days_ago = (date.today() - date.fromisoformat(latest_date)).days
    
    if days_ago >= 5:
        hours = db.hours_since_last_notification("inactive")
        if hours < 48:
            return 0, ""
        return 2, f"No training in {days_ago} days"
    elif days_ago >= 3:
        hours = db.hours_since_last_notification("inactive")
        if hours < 48:
            return 0, ""
        return 1, f"No training in {days_ago} days"
    return 0, ""


def should_notify(db: Database) -> tuple[bool, list[str], int]:
    """Compute urgency score from DB state alone. Returns (should_send, events, score)."""
    checks = [
        _check_new_activity,
        _check_ski_pr,
        _check_hrv_trend,
        _check_rhr_elevated,
        _check_inactive,
    ]
    
    total_score = 0
    events = []
    
    for check in checks:
        score, description = check(db)
        if score > 0:
            total_score += score
            events.append(description)
            logger.info("Event: %s (score +%d)", description, score)
    
    # Frequency dampening — don't spam
    hours_since_any = db.hours_since_last_notification()
    if hours_since_any < 6:
        total_score -= 3
        logger.info("Dampened: notification sent %0.1fh ago (-3)", hours_since_any)
    
    threshold = 3
    should_send = total_score >= threshold and len(events) > 0
    
    logger.info("Notification score: %d (threshold %d) -> %s", total_score, threshold, "SEND" if should_send else "skip")
    
    return should_send, events, total_score
