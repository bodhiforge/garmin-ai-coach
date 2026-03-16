"""Coach impact report — measures whether the bot actually changes behavior."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..db.models import Database


def impact_report(db: Database, days: int = 30) -> str:
    """Generate a plain-text impact report. Pure Python, no LLM."""
    since = date.today() - timedelta(days=days)
    since_str = str(since)

    lines = [
        f"Coach Impact Report ({since} to {date.today()})",
        "=" * 50,
    ]

    # --- Notification stats ---
    notifications = db.get_notifications_since(since_str)
    notif_by_type: dict[str, int] = {}
    for n in notifications:
        t = n["type"]
        notif_by_type[t] = notif_by_type.get(t, 0) + 1

    total_notif = len(notifications)
    lines.append(f"\nNotifications sent: {total_notif}")
    for t, count in sorted(notif_by_type.items(), key=lambda x: -x[1]):
        lines.append(f"  {t}: {count}")

    # --- Activity stats ---
    activities = db.get_recent_activities(days=days)
    metrics = db.get_recent_metrics(days=days)

    ski_sessions = [a for a in activities if a["type"] == "skiing"]
    gym_sessions = [a for a in activities if a["type"] == "strength"]

    lines.append(f"\nTraining: {len(activities)} sessions in {days} days")
    if ski_sessions:
        lines.append(f"  Skiing: {len(ski_sessions)} sessions")
    if gym_sessions:
        lines.append(f"  Gym: {len(gym_sessions)} sessions")

    # Training frequency (sessions per week)
    weeks = max(1, days / 7)
    freq = len(activities) / weeks
    lines.append(f"  Frequency: {freq:.1f} sessions/week")

    # --- Ski speed progression ---
    if len(ski_sessions) >= 2:
        lines.append("\nSki Speed:")
        session_speeds = []
        for a in ski_sessions:
            runs = db.get_ski_runs(a["id"])
            if runs:
                speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
                session_speeds.append((a["date"], max(speeds)))

        if len(session_speeds) >= 2:
            session_speeds.sort()  # chronological
            first_speed = session_speeds[0][1]
            last_speed = session_speeds[-1][1]
            best_speed = max(s[1] for s in session_speeds)
            change = last_speed - first_speed
            change_pct = (change / first_speed * 100) if first_speed > 0 else 0
            lines.append(f"  First session: {first_speed:.1f} km/h")
            lines.append(f"  Latest session: {last_speed:.1f} km/h ({change:+.1f}, {change_pct:+.0f}%)")
            lines.append(f"  Season best: {best_speed:.1f} km/h")

    # --- Run budget compliance ---
    _run_budget_section(db, ski_sessions, lines)

    # --- Recovery compliance ---
    _recovery_compliance_section(db, metrics, activities, notifications, lines)

    # --- Sleep trend ---
    _sleep_section(metrics, lines)

    # --- Gym progression ---
    if len(gym_sessions) >= 2:
        _gym_section(db, gym_sessions, lines)

    return "\n".join(lines)


def _run_budget_section(
    db: Database, ski_sessions: list[dict], lines: list[str],
) -> None:
    """Check if user followed fatigue patterns — stopped before speed dropped."""
    if len(ski_sessions) < 2:
        return

    lines.append("\nRun Budget Compliance:")
    good = 0
    over = 0
    for a in ski_sessions:
        runs = db.get_ski_runs(a["id"])
        if not runs or len(runs) < 3:
            continue
        speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
        peak = max(speeds)
        # Find where speed drops >15%
        decline_run = None
        for i, s in enumerate(speeds):
            if i > 0 and s < peak * 0.85:
                decline_run = i + 1
                break
        if decline_run is not None and len(runs) > decline_run + 2:
            over += 1
        else:
            good += 1

    total = good + over
    if total > 0:
        lines.append(f"  Stopped before fatigue: {good}/{total} ({good/total*100:.0f}%)")
        if over > 0:
            lines.append(f"  Pushed past fatigue point: {over} sessions")
    else:
        lines.append("  Not enough run data to assess")


def _recovery_compliance_section(
    db: Database,
    metrics: list[dict],
    activities: list[dict],
    notifications: list[dict],
    lines: list[str],
) -> None:
    """Check if user rested on LOW readiness days."""
    lines.append("\nRecovery Compliance:")

    low_days = []
    for m in metrics:
        tr = m.get("training_readiness_score")
        if tr is not None and tr < 40:
            low_days.append(m["date"])

    if not low_days:
        # Fallback: check HRV dips
        hrvs = [m for m in metrics if m.get("hrv_last_night") is not None]
        if len(hrvs) >= 5:
            avg_hrv = sum(m["hrv_last_night"] for m in hrvs) / len(hrvs)
            low_days = [
                m["date"] for m in hrvs
                if m["hrv_last_night"] < avg_hrv * 0.85
            ]

    if not low_days:
        lines.append("  No low-readiness days detected")
        return

    activity_dates = set(a["date"] for a in activities)
    rested = sum(1 for d in low_days if d not in activity_dates)
    trained = sum(1 for d in low_days if d in activity_dates)

    lines.append(f"  Low readiness days: {len(low_days)}")
    lines.append(f"  Rested on low days: {rested}/{len(low_days)} ({rested/len(low_days)*100:.0f}%)")
    if trained > 0:
        lines.append(f"  Trained despite low readiness: {trained}")


def _sleep_section(metrics: list[dict], lines: list[str]) -> None:
    """Sleep quality trends."""
    sleeps = [
        m.get("sleep_duration_min")
        for m in metrics
        if m.get("sleep_duration_min") is not None
    ]
    if len(sleeps) < 7:
        return

    lines.append("\nSleep:")
    avg = sum(sleeps) / len(sleeps)
    under_7h = sum(1 for s in sleeps if s < 420)
    lines.append(f"  Average: {avg/60:.1f}h")
    lines.append(f"  Under 7h: {under_7h}/{len(sleeps)} nights ({under_7h/len(sleeps)*100:.0f}%)")

    # Trend: compare first half vs second half
    mid = len(sleeps) // 2
    first_half_avg = sum(sleeps[:mid]) / mid if mid > 0 else 0
    second_half_avg = sum(sleeps[mid:]) / len(sleeps[mid:]) if len(sleeps[mid:]) > 0 else 0
    if first_half_avg > 0:
        change = (second_half_avg - first_half_avg) / first_half_avg * 100
        direction = "improving" if change > 0 else "declining"
        lines.append(f"  Trend: {direction} ({change:+.0f}%)")


def _gym_section(db: Database, gym_sessions: list[dict], lines: list[str]) -> None:
    """Gym progression — weight changes per exercise."""
    lines.append("\nGym Progression:")

    # Build exercise history
    exercise_data: dict[str, list[tuple[str, float]]] = {}
    for a in gym_sessions:
        sets = db.get_gym_sets(a["id"])
        for s in (sets or []):
            ex = s.get("exercise", "unknown")
            weight = s.get("weight_kg", 0) or 0
            if weight > 0:
                if ex not in exercise_data:
                    exercise_data[ex] = []
                exercise_data[ex].append((a["date"], weight))

    for ex, data in sorted(exercise_data.items()):
        if len(data) < 2:
            continue
        data.sort()
        first_max = data[0][1]
        last_max = data[-1][1]
        best = max(d[1] for d in data)
        change = last_max - first_max
        name = ex.replace("_", " ").title()
        if change > 0:
            lines.append(f"  {name}: {first_max:.0f} → {last_max:.0f} kg (+{change:.0f})")
        elif change < 0:
            lines.append(f"  {name}: {first_max:.0f} → {last_max:.0f} kg ({change:.0f})")
        else:
            lines.append(f"  {name}: {last_max:.0f} kg (no change)")
