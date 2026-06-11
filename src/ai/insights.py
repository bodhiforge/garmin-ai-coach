"""Computed insights — Python does the math AND the analysis. LLM only presents."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from ..db.models import Database


PERIOD_PHASES = {"period", "menstrual", "menstruation", "menses", "1", "phase_1"}
OUTDOOR_SLOT_ACTIVITY_TYPES = {
    "hiking",
    "walking",
    "cycling",
    "tennis",
    "skiing",
    "surfing",
    "camping",
}
HIGH_IMPACT_ACTIVITY_TYPES = {"running", "basketball", "tennis", "skiing"}
PRIORITY_STRENGTH_GROUPS = ("back", "shoulders", "posterior_chain", "glutes", "quads", "core")
MOVEMENT_PATTERN_BY_GROUP = {
    "back": "horizontal/vertical pull (rows, pulldowns)",
    "shoulders": "scapular control + shoulder health (face pulls, controlled press)",
    "posterior_chain": "hinge pattern (RDL, hip hinge)",
    "glutes": "hip extension (hip thrust, glute bridge)",
    "core": "anti-rotation / trunk control (Pallof press, dead bug)",
    "quads": "controlled quad / single-leg exposure (leg press, split squat, step-up; low-to-moderate volume)",
}
WEEKLY_PLAN_ENV = "GARMIN_WEEKLY_PLAN_PATH"
HOME_MICRO_SESSION = (
    "home micro-session (90/90 breathing, dead bug regression, wall slide/wall angel, "
    "glute bridge hold, optional right-ankle balance)"
)
RECENT_STRENGTH_OVERRIDE_HOURS = 36
HIGH_VOLUME_STRENGTH_SET_THRESHOLD = 18
OVERLAP_STRENGTH_GROUPS = {"back", "shoulders", "posterior_chain", "glutes", "core", "quads"}
RECOVERY_PLAN_TYPES = {"recovery", "recovery_skill", "rest", "active_recovery"}


def _weekly_plan_path() -> Path:
    return Path(os.environ.get(WEEKLY_PLAN_ENV, Path.home() / "ai" / "data" / "weekly-plan.md"))


def _planned_session_for_date(target_date: date | None = None) -> dict[str, Any] | None:
    """Return the weekly-plan session for target_date, if one exists."""
    day = target_date or date.today()
    plan_path = _weekly_plan_path()
    if not plan_path.exists():
        return None

    text = plan_path.read_text()
    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return None

    sessions = data.get("sessions") or []
    for session in sessions:
        session_date = session.get("date")
        if str(session_date) != day.isoformat():
            continue
        status = str(session.get("status") or "planned").lower()
        if status in {"cancelled", "canceled", "skipped"}:
            return None
        return session

    return None


def _planned_session_type(target_date: date | None = None) -> str:
    session = _planned_session_for_date(target_date)
    return str((session or {}).get("type") or "").lower()


def _is_recovery_plan_type(planned_type: str) -> bool:
    return planned_type in RECOVERY_PLAN_TYPES or "recovery" in planned_type


def _groups_from_training_text(text: object) -> set[str]:
    normalized = _normalized_exercise_name(text)
    groups: set[str] = set()
    for exercise_key, group_map in _MUSCLE_MAP_BY_EXERCISE.items():
        if exercise_key in normalized:
            groups.update(group_map.keys())
    return groups


def _planned_strength_groups(planned_session: dict[str, Any] | None) -> set[str]:
    if planned_session is None:
        return set()

    planned_type = str(planned_session.get("type") or "").lower()
    type_groups: set[str] = set()
    if "lower" in planned_type or "posterior" in planned_type:
        type_groups.update({"posterior_chain", "glutes", "quads", "core"})
    if "upper" in planned_type or "pull" in planned_type:
        type_groups.update({"back", "shoulders", "core"})

    prescription_groups = _groups_from_training_text(planned_session.get("prescription") or "")
    return type_groups | prescription_groups


def _activity_hours_before_target(activity: dict[str, Any], target_date: date) -> float | None:
    activity_date = activity.get("date")
    if activity_date is None:
        return None
    try:
        day_delta = (target_date - date.fromisoformat(str(activity_date))).days
    except ValueError:
        return None
    if day_delta < 0:
        return None
    # Date-only Garmin summaries are not precise enough for same-day sequencing.
    # Treat yesterday as roughly 24h before the target day, which is the key
    # recovery boundary for morning pushes.
    return float(day_delta * 24)


def _strength_activity_summary(db: Database, activity: dict[str, Any]) -> dict[str, Any]:
    sets = db.get_gym_sets(activity["id"])
    ignored_keywords = ("unknown", "treadmill", "cardio")
    strength_sets = [
        set_row for set_row in sets
        if not any(keyword in _normalized_exercise_name(set_row.get("exercise")) for keyword in ignored_keywords)
        and (set_row.get("reps") or 0) > 0
    ]

    group_load: dict[str, float] = {}
    exercises = sorted({
        str(set_row.get("exercise"))
        for set_row in strength_sets
        if set_row.get("exercise")
    })
    unmapped_exercises = set()
    for set_row in strength_sets:
        group_map = _exercise_group_map(set_row.get("exercise"))
        if not group_map:
            if set_row.get("exercise"):
                unmapped_exercises.add(str(set_row.get("exercise")))
            continue
        for group, share in group_map.items():
            group_load[group] = group_load.get(group, 0) + share

    top_groups = [
        group for group, _value in sorted(group_load.items(), key=lambda item: item[1], reverse=True)[:6]
    ]
    return {
        "date": str(activity.get("date") or "unknown"),
        "duration_min": activity.get("duration_min") or 0,
        "training_load": activity.get("training_load") or 0,
        "set_count": len(strength_sets),
        "rep_count": sum(set_row.get("reps") or 0 for set_row in strength_sets),
        "groups": top_groups,
        "exercises": exercises,
        "unmapped_exercises": sorted(unmapped_exercises),
    }


def _latest_strength_summary(db: Database, target_date: date) -> dict[str, Any] | None:
    for activity in db.get_recent_activities(days=14, activity_type="strength"):
        hours_before = _activity_hours_before_target(activity, target_date)
        if hours_before is None:
            continue
        summary = _strength_activity_summary(db, activity)
        summary["hours_before_target"] = hours_before
        return summary
    return None


def _adaptive_plan_override(
    db: Database,
    metrics: dict,
    planned_session: dict[str, Any] | None,
    target_date: date | None = None,
) -> dict[str, Any] | None:
    """Return a professional-plan override when yesterday's actual work beats the template."""
    day = target_date or date.today()
    planned_type = str((planned_session or {}).get("type") or "").lower()
    if "strength" not in planned_type:
        return None

    latest = _latest_strength_summary(db, day)
    if latest is None:
        return None
    if latest["hours_before_target"] > RECENT_STRENGTH_OVERRIDE_HOURS:
        return None

    actual_groups = set(latest["groups"])
    planned_groups = _planned_strength_groups(planned_session)
    overlapping_groups = sorted((actual_groups & planned_groups) & OVERLAP_STRENGTH_GROUPS)
    if len(overlapping_groups) < 2:
        return None

    acwr = metrics.get("acwr_ratio") or 0
    set_count = latest["set_count"]
    duration_min = latest["duration_min"]
    high_volume = set_count >= HIGH_VOLUME_STRENGTH_SET_THRESHOLD or duration_min >= 55
    elevated_load = acwr > 1.3
    if not high_volume and not elevated_load:
        return None

    reasons = []
    if high_volume:
        reasons.append(f"last strength was {duration_min:.0f} min / {set_count} sets")
    if elevated_load:
        reasons.append(f"ACWR {acwr:.2f} > 1.30")

    return {
        "latest": latest,
        "planned_type": planned_type,
        "planned_groups": sorted(planned_groups),
        "overlapping_groups": overlapping_groups,
        "reasons": reasons,
        "eligible_modalities": [
            "swim technique or easy Zone 1-2 cardio",
            "core-control micro work",
            "non-overlap gym technique only if the user insists on gym",
        ],
    }


def _sleep_debt_minutes(db: Database, *, days: int = 7, target_sleep_min: int = 420) -> int:
    """Calculate sleep debt from recorded sleep nights only; missing data is unknown, not zero sleep."""
    entries = db.get_sleep_stages(days=days) or db.get_recent_metrics(days=days) or []
    sleep_values = [
        entry.get("sleep_duration_min")
        for entry in entries
        if entry.get("sleep_duration_min") is not None
    ]
    return sum(max(0, target_sleep_min - int(sleep_min)) for sleep_min in sleep_values)


def ski_insights(db: Database) -> str:
    activities = db.get_recent_activities(days=365, activity_type="skiing")
    if not activities:
        return "No ski data."

    sessions = []
    season_max_speed = 0
    season_max_date = ""
    all_runs = []

    for a in activities:
        runs = db.get_ski_runs(a["id"])
        if not runs:
            continue
        speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
        drops = [r.get("vertical_drop_m", 0) or 0 for r in runs]
        max_speed = max(speeds) if speeds else 0
        total_drop = sum(drops)

        if max_speed > season_max_speed:
            season_max_speed = max_speed
            season_max_date = a["date"]

        # Per-run speed analysis
        run_speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
        best_run = max(range(len(runs)), key=lambda i: run_speeds[i]) + 1 if runs else 0
        worst_run = min(range(len(runs)), key=lambda i: run_speeds[i]) + 1 if runs else 0

        # Fatigue: speed drop in second half
        mid = max(1, len(run_speeds) // 2)
        first_half_avg = sum(run_speeds[:mid]) / mid if mid > 0 else 0
        second_half_avg = sum(run_speeds[mid:]) / len(run_speeds[mid:]) if len(run_speeds[mid:]) > 0 else 0
        fatigue_pct = ((first_half_avg - second_half_avg) / first_half_avg * 100) if first_half_avg > 0 else 0

        # Find exact run where speed started dropping
        decline_run = None
        if len(run_speeds) >= 3:
            peak_idx = run_speeds.index(max(run_speeds))
            for i in range(peak_idx + 1, len(run_speeds)):
                if run_speeds[i] < max(run_speeds) * 0.85:
                    decline_run = i + 1
                    break

        # HR recovery trend
        lift_hrs = [r.get("lift_top_hr") for r in runs if r.get("lift_top_hr") is not None]
        hr_recovery_issue = None
        if len(lift_hrs) >= 3:
            first_hr = lift_hrs[0]
            last_hr = lift_hrs[-1]
            if last_hr > first_hr * 1.15:
                hr_recovery_issue = f"HR recovery worsened ({first_hr}→{last_hr}bpm at lift top)"

        sessions.append({
            "date": a["date"],
            "runs": len(runs),
            "max_speed": max_speed,
            "avg_speed": sum(run_speeds) / len(run_speeds) if run_speeds else 0,
            "total_drop": total_drop,
            "duration_min": a.get("duration_min", 0),
            "fatigue_pct": fatigue_pct,
            "decline_run": decline_run,
            "hr_recovery_issue": hr_recovery_issue,
            "best_run": best_run,
            "run_speeds": run_speeds,
        })
        all_runs.extend(runs)

    if not sessions:
        return "No ski run data."

    # === DERIVED ANALYSIS (not just stats) ===

    # Speed progression
    session_speeds = [s["max_speed"] for s in sessions]  # newest first
    oldest_speed = session_speeds[-1]
    newest_speed = session_speeds[0]
    speed_change_pct = ((newest_speed - oldest_speed) / oldest_speed * 100) if oldest_speed > 0 else 0

    # Plateau detection: last 2 sessions within 5% of each other
    plateau = False
    if len(session_speeds) >= 3:
        recent_2 = session_speeds[:2]
        if abs(recent_2[0] - recent_2[1]) / max(recent_2) < 0.05:
            plateau = True

    # Speed target gap (use 35 km/h as minimum competitive target)
    speed_target = 35.0
    gap = speed_target - season_max_speed
    gap_pct = (gap / speed_target * 100) if speed_target > 0 else 0

    # Bottleneck analysis
    avg_fatigue = sum(s["fatigue_pct"] for s in sessions) / len(sessions)
    hr_issues = [s for s in sessions if s["hr_recovery_issue"] is not None]
    bottleneck = "unknown"
    if avg_fatigue < 5 and not hr_issues:
        bottleneck = "technique (fitness is not the limiter — speed plateau with good HR recovery suggests technique is the bottleneck)"
    elif avg_fatigue > 15:
        bottleneck = "endurance (significant speed drops in later runs)"
    elif hr_issues:
        bottleneck = "recovery (HR not recovering between runs — fitness or fatigue)"

    # Optimal session length across all sessions
    sessions_with_decline = [s for s in sessions if s["decline_run"] is not None]
    optimal_runs = None
    if sessions_with_decline:
        optimal_runs = min(s["decline_run"] - 1 for s in sessions_with_decline)

    # === BUILD OUTPUT ===
    lines = [
        "## Ski Analysis (computed — all numbers verified by Python)",
        "",
        "### Progress",
        f"Speed: {oldest_speed:.1f} → {newest_speed:.1f} km/h ({speed_change_pct:+.0f}% over {len(sessions)} sessions)",
        f"Season best: {season_max_speed:.1f} km/h ({season_max_date})",
    ]

    if plateau:
        lines.append(f"⚠️ PLATEAU DETECTED: last 2 sessions within 5% ({session_speeds[1]:.1f} → {session_speeds[0]:.1f})")

    lines.append(f"Target: {speed_target:.0f} km/h — gap: {gap:.1f} km/h ({gap_pct:.0f}% remaining)")
    lines.append(f"Total: {len(sessions)} sessions, {len(all_runs)} runs")
    lines.append("")

    # Bottleneck
    lines.append(f"### Bottleneck: {bottleneck}")
    lines.append("")

    # Fatigue pattern
    lines.append("### Fatigue Pattern")
    if optimal_runs is not None:
        lines.append(f"Performance declines after run {optimal_runs} — keep sessions to {optimal_runs} quality runs")
    else:
        lines.append("No consistent fatigue pattern detected yet")

    if hr_issues:
        for s in hr_issues:
            lines.append(f"  {s['date']}: {s['hr_recovery_issue']}")
    lines.append("")

    # Per-session detail
    lines.append("### Sessions")
    for s in sessions:
        speed_list = " → ".join(f"{sp:.0f}" for sp in s["run_speeds"])
        fatigue_str = ""
        if s["fatigue_pct"] > 5:
            fatigue_str = f" | speed dropped {s['fatigue_pct']:.0f}% in second half"
        if s["decline_run"] is not None:
            fatigue_str += f" (from run {s['decline_run']})"
        hr_str = f" | {s['hr_recovery_issue']}" if s["hr_recovery_issue"] else ""
        lines.append(
            f"  {s['date']}: {s['runs']} runs | "
            f"max {s['max_speed']:.1f} km/h | "
            f"drop {s['total_drop']:.0f}m | "
            f"speeds [{speed_list}]{fatigue_str}{hr_str}"
        )

    # Actionable conclusions
    lines.append("")
    lines.append("### Conclusions")
    if plateau:
        lines.append("- Speed has plateaued. To break through, you need to practice at higher speeds on moderate terrain, not just accumulate runs.")
    if bottleneck == "technique (fitness is not the limiter — speed plateau with good HR recovery suggests technique is the bottleneck)":
        lines.append("- Your fitness is fine (good HR recovery). The speed limit is technique-based. Focus on carving quality, not volume.")
    if gap > 0:
        lines.append(f"- {gap:.1f} km/h to speed target. At current progression rate, {'achievable this season' if gap < 10 else 'may take another season'}.")
    if optimal_runs is not None:
        lines.append(f"- Best quality in first {optimal_runs} runs. After that, focus on easy cruising or stop.")
    else:
        lines.append("- No fatigue limit found yet — you can handle more runs per session.")

    return "\n".join(lines)


def gym_insights(db: Database) -> str:
    activities = db.get_recent_activities(days=365, activity_type="strength")
    if not activities:
        return "No gym data."

    exercise_history: dict[str, list[dict]] = {}
    total_sessions = 0

    for a in activities:
        sets = db.get_gym_sets(a["id"])
        if not sets:
            continue
        total_sessions += 1
        for s in sets:
            ex = s.get("exercise", "unknown")
            weight = s.get("weight_lb")
            reps = s.get("reps")
            if weight is not None and reps is not None:
                if ex not in exercise_history:
                    exercise_history[ex] = []
                exercise_history[ex].append({
                    "date": a["date"],
                    "weight": weight,
                    "reps": reps,
                    "volume": weight * reps,
                })

    if not exercise_history:
        return f"Gym sessions: {total_sessions}, but no weight/rep data recorded. Record weights on your watch for tracking."

    lines = [
        "## Gym Analysis (computed)",
        f"Sessions: {total_sessions} | Exercises tracked: {len(exercise_history)}",
        "",
    ]

    for ex_name, history in sorted(exercise_history.items()):
        if len(history) < 2:
            latest = history[0]
            lines.append(f"  {ex_name}: {latest['weight']}lb × {latest['reps']} ({latest['date']}) — need more data")
            continue

        first = history[-1]
        last = history[0]
        weight_change = last["weight"] - first["weight"]
        volume_change_pct = ((last["volume"] - first["volume"]) / first["volume"] * 100) if first["volume"] > 0 else 0

        # Plateau detection
        if len(history) >= 3 and all(h["weight"] == history[0]["weight"] for h in history[:3]):
            lines.append(f"  {ex_name}: {last['weight']}lb × {last['reps']} — ⚠️ PLATEAU (same weight 3+ sessions). Increase weight or reps.")
        elif weight_change > 0:
            lines.append(f"  {ex_name}: {first['weight']}→{last['weight']}lb (+{weight_change}lb) | volume {volume_change_pct:+.0f}%")
        else:
            lines.append(f"  {ex_name}: {first['weight']}→{last['weight']}lb ({weight_change:+.0f}lb)")

    return "\n".join(lines)


def recovery_insights(db: Database) -> str:
    metrics = db.get_recent_metrics(days=14)
    if not metrics:
        return "No recovery data."

    hrvs = [m.get("hrv_last_night") for m in metrics if m.get("hrv_last_night") is not None]
    sleeps = [m.get("sleep_duration_min") for m in metrics if m.get("sleep_duration_min") is not None]
    rhrs = [m.get("resting_hr") for m in metrics if m.get("resting_hr") is not None]
    bbs = [m.get("body_battery_am") for m in metrics if m.get("body_battery_am") is not None]

    lines = ["## Recovery Analysis (computed)"]

    # Garmin Training Readiness (authoritative when available)
    latest = metrics[0]
    tr_score = latest.get("training_readiness_score")
    tr_level = latest.get("training_readiness_level")
    recovery_hours = latest.get("recovery_time_hours")
    acute_load = latest.get("acute_load")

    if tr_score is not None:
        lines.append(f"Garmin Training Readiness: {tr_score}/100 ({tr_level})")
        if recovery_hours is not None and recovery_hours > 0:
            lines.append(f"  Recovery time remaining: {recovery_hours}h")
        if acute_load is not None:
            lines.append(f"  Acute training load: {acute_load:.0f}")

    # HRV analysis
    if hrvs:
        avg_hrv = sum(hrvs) / len(hrvs)
        latest_hrv = hrvs[0]
        hrv_vs_avg = ((latest_hrv - avg_hrv) / avg_hrv * 100) if avg_hrv > 0 else 0
        lines.append(f"HRV: {latest_hrv:.0f}ms (avg {avg_hrv:.0f}ms, {hrv_vs_avg:+.0f}%)")

        if len(hrvs) >= 3:
            recent_3 = hrvs[:3]
            if all(recent_3[i] <= recent_3[i+1] for i in range(len(recent_3)-1)):
                lines.append("  ⚠️ HRV declining 3 days — accumulated fatigue signal")
            elif all(recent_3[i] >= recent_3[i+1] for i in range(len(recent_3)-1)):
                lines.append("  ✅ HRV rising 3 days — good recovery trend")

    # Sleep
    if sleeps:
        avg_sleep = sum(sleeps) / len(sleeps)
        latest_sleep = sleeps[0]
        lines.append(f"Sleep: {latest_sleep // 60}h{latest_sleep % 60:02d}m (avg {avg_sleep // 60:.0f}h{avg_sleep % 60:02.0f}m)")
        if latest_sleep < 360:
            lines.append("  ⚠️ Under 6h — expect 10-20% performance drop")
        elif latest_sleep < 420:
            lines.append("  ⚠️ Under 7h — suboptimal for recovery")

    # RHR
    if rhrs:
        avg_rhr = sum(rhrs) / len(rhrs)
        latest_rhr = rhrs[0]
        rhr_diff = latest_rhr - avg_rhr
        lines.append(f"Resting HR: {latest_rhr}bpm (avg {avg_rhr:.0f}bpm, {rhr_diff:+.0f})")
        if rhr_diff > 3:
            lines.append("  ⚠️ RHR elevated 3+ bpm above avg — fatigue, stress, or illness")

    if bbs:
        lines.append(f"Body Battery: {bbs[0]}/100")

    # Readiness verdict — prefer Garmin Training Readiness when available
    lines.append("")
    if tr_score is not None:
        if tr_score >= 65:
            lines.append(f"Readiness: GOOD ({tr_score}/100) — ready for high intensity training")
        elif tr_score >= 40:
            lines.append(f"Readiness: MODERATE ({tr_score}/100) — train but reduce intensity")
        else:
            lines.append(f"Readiness: LOW ({tr_score}/100) — recovery day recommended")
    else:
        # Fallback to our own HRV/sleep/RHR heuristic
        issues = []
        if hrvs and ((hrvs[0] - sum(hrvs) / len(hrvs)) / (sum(hrvs) / len(hrvs)) * 100) < -10:
            issues.append("HRV significantly below avg")
        if sleeps and sleeps[0] < 360:
            issues.append("sleep under 6h")
        if rhrs and rhrs[0] - sum(rhrs) / len(rhrs) > 5:
            issues.append("RHR elevated")

        if not issues:
            lines.append("Readiness: GOOD — ready for high intensity training")
        elif len(issues) == 1:
            lines.append(f"Readiness: MODERATE — {issues[0]}. Train but reduce intensity.")
        else:
            lines.append(f"Readiness: LOW — {', '.join(issues)}. Recovery day recommended.")

    return "\n".join(lines)


def pre_ski_briefing(db: Database) -> str | None:
    """If user skied in the last 2 days, return a run budget briefing. Otherwise None."""
    recent_ski = db.get_recent_activities(days=2, activity_type="skiing")
    if not recent_ski:
        return None

    latest = recent_ski[0]
    days_since = (date.today() - date.fromisoformat(latest["date"])).days

    # Get all season data for context
    all_ski = db.get_recent_activities(days=365, activity_type="skiing")

    # Count consecutive recent ski days
    ski_dates = sorted(set(a["date"] for a in all_ski), reverse=True)
    consecutive = 0
    check_date = date.today()
    for _ in range(7):
        if str(check_date) in ski_dates or str(check_date - timedelta(days=0)) in ski_dates:
            consecutive += 1
        else:
            break
        check_date -= timedelta(days=1)
    # Don't count today (hasn't happened yet)
    consecutive = max(0, consecutive)

    # Compute run budget from fatigue patterns
    optimal_runs = None
    for a in all_ski:
        runs = db.get_ski_runs(a["id"])
        if not runs or len(runs) < 3:
            continue
        run_speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
        peak_speed = max(run_speeds)
        for i, speed in enumerate(run_speeds):
            if i > 0 and speed < peak_speed * 0.85:
                if optimal_runs is None or i < optimal_runs:
                    optimal_runs = i
                break

    # Yesterday's fatigue
    yesterday_fatigue = None
    if days_since <= 1:
        runs = db.get_ski_runs(latest["id"])
        if runs and len(runs) >= 2:
            run_speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
            mid = max(1, len(run_speeds) // 2)
            first_avg = sum(run_speeds[:mid]) / mid
            second_avg = sum(run_speeds[mid:]) / len(run_speeds[mid:])
            if first_avg > 0:
                yesterday_fatigue = (first_avg - second_avg) / first_avg * 100

    lines = ["## Pre-Ski Briefing (consecutive skiing detected)"]

    if days_since == 0:
        lines.append(f"Already skied today.")
    elif days_since == 1:
        lines.append(f"Skied yesterday ({latest['date']}).")
    else:
        lines.append(f"Last ski: {latest['date']} ({days_since} days ago).")

    if consecutive >= 2:
        lines.append(f"⚠️ {consecutive} consecutive ski days — accumulated fatigue expected.")
        lines.append("Reduce run count by 20-30% from your normal session.")

    if optimal_runs is not None:
        budget = optimal_runs
        if consecutive >= 2:
            budget = max(2, optimal_runs - 1)
        lines.append(f"Run budget today: {budget} quality runs (performance typically drops after run {optimal_runs}).")
    else:
        lines.append("Not enough data to compute run budget yet.")

    if yesterday_fatigue is not None and yesterday_fatigue > 10:
        lines.append(f"Yesterday's fatigue: speed dropped {yesterday_fatigue:.0f}% in second half — start easy today.")

    return "\n".join(lines)


def training_accountability(db: Database) -> str:
    """Training frequency, recency, and push signals for the AI coach."""
    lines = ["## Training Accountability (computed)"]
    
    # Days since last workout (any type)
    all_recent = db.get_recent_activities(days=30)
    if all_recent:
        last_date = date.fromisoformat(all_recent[0]["date"])
        days_since = (date.today() - last_date).days
        lines.append(f"Last workout: {all_recent[0]['type']} on {all_recent[0]['date']} ({days_since} day(s) ago)")
        if days_since >= 3:
            lines.append(f"  \u26a0\ufe0f {days_since} days without training. Push today if readiness allows.")
        elif days_since == 0:
            lines.append("  Already trained today.")
    else:
        lines.append("No workouts in the last 30 days. Time to start.")
    
    # This week's training volume
    week_start = date.today() - timedelta(days=date.today().weekday())
    this_week = [a for a in all_recent if date.fromisoformat(a["date"]) >= week_start]
    week_types = [a["type"] for a in this_week]
    target_sessions = 3  # default weekly target
    lines.append(f"This week: {len(this_week)}/{target_sessions} sessions ({', '.join(week_types) if week_types else 'none yet'})")
    remaining_days = 6 - date.today().weekday()
    if len(this_week) < target_sessions and remaining_days > 0:
        needed = target_sessions - len(this_week)
        lines.append(f"  Need {needed} more session(s) in {remaining_days} remaining day(s).")
    
    # 2-week training rhythm
    two_weeks = db.get_recent_activities(days=14)
    week1 = [a for a in two_weeks if date.fromisoformat(a["date"]) >= date.today() - timedelta(days=7)]
    week2 = [a for a in two_weeks if date.fromisoformat(a["date"]) < date.today() - timedelta(days=7)]
    lines.append(f"Training rhythm: this week {len(week1)} | last week {len(week2)} sessions")
    if len(week1) < len(week2):
        lines.append("  Frequency dropping. Maintain consistency.")
    
    return "\n".join(lines)




def sleep_quality_insights(db) -> str:
    """Deep sleep analysis using v4 stage data."""
    from datetime import date
    stages = db.get_sleep_stages(days=7)
    if not stages:
        return ""

    lines = ["## Sleep Quality (computed)"]
    today = stages[0]
    total = today.get("sleep_duration_min") or 0

    deep = today.get("sleep_deep_min") or 0
    light = today.get("sleep_light_min") or 0
    rem = today.get("sleep_rem_min") or 0
    awake = today.get("sleep_awake_min") or 0

    deep_pct = round(deep / total * 100) if total > 0 else 0
    rem_pct = round(rem / total * 100) if total > 0 else 0

    lines.append(f"Last night: deep {deep}m ({deep_pct}%) | light {light}m | REM {rem}m ({rem_pct}%) | awake {awake}m")

    # Score breakdown
    score = today.get("sleep_score") or 0
    restlessness = today.get("sleep_score_restlessness") or "?"
    restless_count = today.get("restless_moments") or 0
    bb_charge = today.get("bb_sleep_charge") or 0
    lines.append(f"Score: {score}/100 | Restlessness: {restlessness} ({restless_count} moments) | BB recharged: +{bb_charge}")

    # Deep sleep assessment
    # Target: 15-25% of total sleep should be deep
    if deep_pct < 15:
        lines.append(f"  ⚠️ Deep sleep {deep_pct}% — below 15% target. Recovery and muscle repair compromised.")
    elif deep_pct >= 20:
        lines.append(f"  ✅ Deep sleep {deep_pct}% — excellent recovery quality.")

    # REM assessment (target: 20-25%)
    if rem_pct < 15:
        lines.append(f"  ⚠️ REM {rem_pct}% — below target. May affect learning consolidation.")

    # 7-day sleep trends
    if len(stages) >= 3:
        recorded_stages = [s for s in stages if s.get("sleep_duration_min") is not None]
        avg_deep = (
            sum((s.get("sleep_deep_min") or 0) for s in recorded_stages) / len(recorded_stages)
            if recorded_stages else 0
        )
        avg_total = (
            sum((s.get("sleep_duration_min") or 0) for s in recorded_stages) / len(recorded_stages)
            if recorded_stages else 0
        )
        avg_deep_pct = round(avg_deep / avg_total * 100) if avg_total > 0 else 0
        avg_bb_charge = (
            sum((s.get("bb_sleep_charge") or 0) for s in recorded_stages) / len(recorded_stages)
            if recorded_stages else 0
        )

        lines.append(f"7-day recorded avg: deep {avg_deep:.0f}m ({avg_deep_pct}%) | total {avg_total:.0f}m | BB charge {avg_bb_charge:.0f}")

        # Sleep debt
        target_min = 420  # 7 hours
        debt_per_night = [(target_min - s.get("sleep_duration_min")) for s in recorded_stages]
        total_debt = sum(max(0, d) for d in debt_per_night)
        if total_debt > 120:
            lines.append(f"  ⚠️ Sleep debt: {total_debt:.0f}m ({total_debt/60:.1f}h) accumulated over {len(recorded_stages)} recorded nights")

    return "\n".join(lines)


def bb_dynamics_insights(db) -> str:
    """Body Battery charge/drain analysis using v4 data."""
    dynamics = db.get_bb_dynamics(days=7)
    if not dynamics:
        return ""

    lines = ["## Body Battery Dynamics (computed)"]
    today = dynamics[0]

    at_wake = today.get("bb_at_wake") or 0
    highest = today.get("bb_highest") or 0
    lowest = today.get("bb_lowest") or 0
    drained = today.get("bb_drained") or 0
    charge = today.get("bb_sleep_charge") or 0
    feedback = today.get("bb_feedback") or ""

    lines.append(f"Today: wake {at_wake} | high {highest} | low {lowest} | drained {drained} | sleep charge +{charge}")
    if feedback:
        lines.append(f"Garmin feedback: {feedback}")

    # Charge efficiency (how much BB gained per hour of sleep)
    # We can estimate from sleep duration if available
    if charge > 0:
        if charge >= 50:
            lines.append("  ✅ Strong overnight recharge — body recovering well")
        elif charge < 30:
            lines.append("  ⚠️ Weak overnight recharge (<30) — poor recovery quality despite sleep")

    # 7-day trend
    if len(dynamics) >= 3:
        avg_wake = sum((d.get("bb_at_wake") or 0) for d in dynamics) / len(dynamics)
        avg_charge = sum((d.get("bb_sleep_charge") or 0) for d in dynamics) / len(dynamics)
        lines.append(f"7-day avg: wake BB {avg_wake:.0f} | charge {avg_charge:.0f}")

        # Declining wake BB trend
        recent_3_wake = [d.get("bb_at_wake") or 0 for d in dynamics[:3]]
        if len(recent_3_wake) == 3 and all(recent_3_wake[i] <= recent_3_wake[i+1] for i in range(2)):
            lines.append("  ⚠️ Wake BB declining 3 days — accumulated fatigue or poor sleep quality")

    return "\n".join(lines)


def readiness_attribution(db) -> str:
    """Explain WHY readiness is what it is using factor breakdown."""
    factors = db.get_readiness_factors(days=3)
    if not factors:
        return ""

    today = factors[0]
    score = today.get("training_readiness_score")
    level = today.get("training_readiness_level")
    if score is None:
        return ""

    lines = [f"## Readiness Attribution (computed)"]
    lines.append(f"Score: {score}/100 ({level})")

    # Factor breakdown
    factor_map = {
        "readiness_sleep_factor": "Sleep",
        "readiness_hrv_factor": "HRV",
        "readiness_recovery_factor": "Recovery",
        "readiness_acwr_factor": "Training Load",
        "readiness_stress_factor": "Stress",
    }

    limiters = []
    strengths = []
    for col, label in factor_map.items():
        val = today.get(col)
        if val is None:
            continue
        if val in ("LOW", "POOR"):
            limiters.append(f"{label}: {val}")
        elif val in ("VERY_GOOD", "EXCELLENT"):
            strengths.append(f"{label}: {val}")

    if limiters:
        lines.append(f"Limiting factors: {', '.join(limiters)}")
    if strengths:
        lines.append(f"Strengths: {', '.join(strengths)}")

    feedback = today.get("readiness_feedback")
    if feedback:
        lines.append(f"Garmin insight: {feedback}")

    recovery_h = today.get("recovery_time_hours")
    if recovery_h is not None and recovery_h > 0:
        lines.append(f"Recovery time remaining: {recovery_h}h")

    # Readiness trend
    if len(factors) >= 3:
        scores = [f.get("training_readiness_score") or 0 for f in factors[:3]]
        if all(scores[i] <= scores[i+1] for i in range(2)):
            lines.append("  ⚠️ Readiness declining 3 days")
        elif all(scores[i] >= scores[i+1] for i in range(2)):
            lines.append("  ✅ Readiness improving 3 days")

    return "\n".join(lines)


def load_with_corrections(db) -> str:
    """Training load analysis with basketball corrections applied."""
    corrected_7d = db.get_corrected_weekly_load(days=7)
    corrected_28d = db.get_corrected_weekly_load(days=28)

    metrics = db.get_recent_metrics(days=1)
    if not metrics:
        return ""

    today = metrics[0]
    acute = today.get("acute_load") or 0
    chronic = today.get("chronic_load") or 0
    acwr = today.get("acwr_ratio") or 0
    status = today.get("training_status") or "?"
    balance = today.get("load_balance") or "?"

    lines = ["## Training Load (computed, basketball-corrected)"]
    lines.append(f"Acute: {acute:.0f} | Chronic: {chronic:.0f} | ACWR: {acwr:.2f} | Status: {status}")
    lines.append(f"Corrected 7-day load: {corrected_7d:.0f} (includes estimated basketball load)")
    lines.append(f"Load balance: {balance}")

    # ACWR zones
    if acwr > 1.3:
        lines.append("  🔴 ACWR > 1.3 — injury risk zone. Reduce training volume.")
    elif acwr > 1.1:
        lines.append("  🟡 ACWR 1.1-1.3 — high load. Monitor recovery closely.")
    elif acwr >= 0.8:
        lines.append("  🟢 ACWR 0.8-1.1 — sweet spot. Progress safely.")
    else:
        lines.append("  ⚪ ACWR < 0.8 — detraining zone. Increase volume if readiness allows.")

    period_active = _is_period_active(today)

    # Load balance interpretation
    if balance and "SHORTAGE" in str(balance):
        if period_active:
            lines.append(
                f"  ⚠️ {balance} — aerobic work is behind, but period is active: "
                "defer swim; use mobility, easy bike, or gentle gym instead"
            )
        else:
            lines.append(f"  ⚠️ {balance} — add more aerobic work (swim, easy run, cycling)")

    return "\n".join(lines)


def _normalize_menstrual_phase(phase: object) -> str:
    if phase is None:
        return ""
    value = str(phase).strip().lower().replace("_", " ")
    return {
        "1": "period",
        "2": "follicular",
        "3": "ovulation",
        "4": "luteal",
    }.get(value, value)


def _is_period_active(metrics: dict | None) -> bool:
    if metrics is None:
        return False
    return _normalize_menstrual_phase(metrics.get("menstrual_phase")) in PERIOD_PHASES


def menstrual_constraint(db: Database, metrics: dict | None = None) -> str:
    """Surface period as a hard training constraint, not generic context."""
    current_metrics = metrics
    if current_metrics is None:
        recent = db.get_recent_metrics(days=1)
        current_metrics = recent[0] if recent else None
    if not _is_period_active(current_metrics):
        return ""

    day = current_metrics.get("menstrual_day_of_cycle")
    day_text = f"cycle day {day}" if day is not None else "cycle day unknown"
    return "\n".join([
        "## Menstrual Constraint (computed — LLM MUST follow)",
        f"Period active ({day_text}).",
        "Hard constraint: do NOT recommend swimming today.",
        "If aerobic/base work is needed, substitute mobility, easy bike, or gentle gym; keep it comfort-based.",
    ])


def concerns_summary(db) -> str:
    """Format active concerns for the LLM."""
    concerns = db.get_active_concerns()
    if not concerns:
        return ""

    lines = ["## Active Concerns (from user)"]
    for c in concerns:
        line = f"- [{c['created_date']}] {c['concern']}"
        if c.get("impact"):
            line += f" → {c['impact']}"
        if c.get("sport_affected"):
            line += f" (affects: {c['sport_affected']})"
        lines.append(line)

    return "\n".join(lines)



def weekly_gap_analysis(db, target_date: date | None = None) -> str:
    """Detect what's missing from this week's training and suggest what to fill."""
    from datetime import date, timedelta

    today = target_date or date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    days_left = 6 - today.weekday()  # remaining days including today

    activities = db.get_recent_activities(days=7)
    this_week = [a for a in activities if date.fromisoformat(a["date"]) >= week_start]
    metrics = db.get_recent_metrics(days=1)
    period_active = _is_period_active(metrics[0] if metrics else None)

    types_done = [a["type"] for a in this_week]
    dates_done = [a["date"] for a in this_week]

    # What's been done
    has_gym = "strength" in types_done
    has_swim = "swimming" in types_done
    has_ski = "skiing" in types_done
    has_basketball = "basketball" in types_done
    gym_count = types_done.count("strength")
    swim_count = types_done.count("swimming")
    outdoor_count = sum(1 for activity_type in types_done if activity_type in OUTDOOR_SLOT_ACTIVITY_TYPES)
    high_impact_recent = any(
        activity_type in HIGH_IMPACT_ACTIVITY_TYPES and date.fromisoformat(activity_date) >= today - timedelta(days=1)
        for activity_type, activity_date in zip(types_done, dates_done)
    )

    # Weekly targets
    # Basketball: 2x (Wed/Fri, fixed)
    # Ski: 1-2x (weather-dependent, bonus)
    # Gym: 2x (fill available days, priority: back/shoulders + lower/core)
    # Swim: 1x minimum (Costa Rica prep, deadline May 2026)
    # Outdoor/adventure: optional 1x (hike, tennis, cycle, camping-day hike)

    lines = ["## Weekly Gap Analysis (computed)"]
    lines.append(f"Done this week: {len(this_week)} sessions ({', '.join(types_done) if types_done else 'none'})")
    lines.append(f"Days left (including today): {days_left + 1}")

    missing = []
    if gym_count < 2:
        needed = 2 - gym_count
        missing.append(f"Gym: need {needed} more (target 2x/week for body recomp)")
    if swim_count < 1:
        if period_active:
            missing.append("Swim: deferred while period is active; use mobility/easy bike/gentle gym for aerobic base")
        else:
            missing.append("Swim: need 1 (Costa Rica freestyle prep — 9 weeks out)")
    if outdoor_count < 1:
        if period_active:
            missing.append("Outdoor/adventure: optional 1x (period-friendly easy hike/easy bike; tennis only if symptoms are low)")
        else:
            missing.append("Outdoor/adventure: optional 1x (hike, tennis, easy cycle, or camping-day hike)")

    if missing:
        lines.append("Missing this week:")
        for m in missing:
            lines.append(f"  - {m}")
        if period_active:
            lines.append("Period constraint: no swim slots suggested until period ends.")

        # Suggest when to fit them
        # Basketball: Wed/Fri evening → morning is free for gym/swim
        # Ski: unpredictable, but usually takes the whole day
        dow = today.weekday()
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        suggestions = []
        outdoor_suggested = outdoor_count >= 1
        for offset in range(days_left + 1):
            check_day = today + timedelta(days=offset)
            check_dow = check_day.weekday()
            check_date = check_day.isoformat()
            planned_session = _planned_session_for_date(check_day)
            planned_type = str((planned_session or {}).get("type") or "").lower()
            planned_prescription = str((planned_session or {}).get("prescription") or "").lower()

            # Skip if already has activity today
            if check_date in dates_done and offset == 0:
                continue
            if planned_type == "basketball" and (
                offset == 0
                or "basketball only" in planned_prescription
                or "no stacked" in planned_prescription
            ):
                continue

            if check_dow in (2, 4):  # Wed/Fri — basketball evening
                if gym_count < 2:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: morning gym (basketball is evening)")
                elif swim_count < 1 and not period_active:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: morning swim (basketball is evening)")
                elif not outdoor_suggested:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: easy bike or mobility only; keep legs fresh for basketball")
                    outdoor_suggested = True
            elif check_dow == 6:  # Sunday — rest preferred
                if swim_count < 1 and not period_active:
                    suggestions.append(f"Sun {check_date}: light swim (active recovery)")
                elif not outdoor_suggested:
                    suggestions.append(f"Sun {check_date}: outdoor recovery slot — easy hike/easy cycle")
                    outdoor_suggested = True
            else:  # Mon/Tue/Thu/Sat — open
                if gym_count < 2:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: gym")
                    gym_count += 1
                elif swim_count < 1 and not period_active:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: swim")
                    swim_count += 1
                elif not outdoor_suggested:
                    if period_active:
                        suggestions.append(f"{dow_names[check_dow]} {check_date}: period-friendly outdoor base — easy hike/easy bike")
                    elif high_impact_recent:
                        suggestions.append(f"{dow_names[check_dow]} {check_date}: easy hike/easy cycle; skip hard tennis after recent high-impact work")
                    else:
                        suggestions.append(f"{dow_names[check_dow]} {check_date}: outdoor slot — hike, tennis, easy cycle, or camping-day hike")
                    outdoor_suggested = True

        if suggestions:
            lines.append("Suggested slots:")
            for s in suggestions[:3]:
                lines.append(f"  → {s}")
    else:
        lines.append("All weekly targets met ✅")

    return "\n".join(lines)


def systemic_strain_check(db, metrics: dict) -> tuple[str | None, list[str]]:
    """Detect physiological stress signals from respiration/HRV/RHR/SpO2/BB vs
    7-day baselines. Returns (severity, signal_descriptions).

    Whoop-style detection without body-temperature (FR955 lacks the sensor).
    Two plausible drivers produce the same signature: illness (infection,
    immune response) or severe training stress (hard session + under-recovery).
    Signals don't distinguish — the correct downstream move is the same:
    train lighter or rest until vitals normalize.

    Thresholds tuned so MODERATE+ flags are rare (target <10% of days).
    """
    history = db.get_recent_metrics(days=8) or []
    if len(history) < 4:
        return (None, [])

    past = [m for m in history if m.get("date") != metrics.get("date")][:7]

    def mean(values):
        filtered = [v for v in values if v is not None]
        return sum(filtered) / len(filtered) if filtered else None

    resp_base = mean([m.get("respiration_avg") for m in past])
    rhr_base = mean([m.get("resting_hr") for m in past])
    bb_base = mean([m.get("body_battery_am") for m in past])
    spo2_base = mean([m.get("spo2_avg") for m in past])
    hrv_base = metrics.get("hrv_weekly_avg") or mean(
        [m.get("hrv_last_night") for m in past]
    )

    resp = metrics.get("respiration_avg")
    rhr = metrics.get("resting_hr")
    hrv = metrics.get("hrv_last_night")
    spo2 = metrics.get("spo2_avg")
    bb_wake = metrics.get("bb_at_wake") or metrics.get("body_battery_am")

    signals: list[str] = []

    if resp is not None and resp_base and resp > resp_base + 1.5:
        signals.append(
            f"respiration {resp:.1f} vs 7d baseline {resp_base:.1f} (+{resp - resp_base:.1f})"
        )
    if hrv is not None and hrv_base and hrv < hrv_base * 0.85:
        signals.append(
            f"HRV {hrv:.0f} vs baseline {hrv_base:.0f} ({(hrv/hrv_base - 1)*100:+.0f}%)"
        )
    if rhr is not None and rhr_base and rhr > rhr_base + 5:
        signals.append(
            f"RHR {rhr} vs baseline {rhr_base:.0f} (+{rhr - rhr_base:.0f} bpm)"
        )
    if spo2 is not None and spo2_base and spo2 < spo2_base - 2:
        signals.append(
            f"SpO2 {spo2:.0f}% vs baseline {spo2_base:.0f}%"
        )
    if bb_wake is not None and bb_base and bb_wake < bb_base * 0.65:
        signals.append(
            f"BB at wake {bb_wake} vs baseline {bb_base:.0f} ({(bb_wake/bb_base - 1)*100:+.0f}%)"
        )

    if not signals:
        return (None, [])
    if len(signals) >= 3:
        return ("HIGH", signals)
    if len(signals) >= 2:
        return ("MODERATE", signals)
    # Single signal is noise; suppress from output.
    return (None, [])


def systemic_strain_block(db, metrics: dict) -> str:
    severity, signals = systemic_strain_check(db, metrics)
    if not signals:
        return ""
    lines = [f"## Systemic Strain Signal: {severity}"]
    for s in signals:
        lines.append(f"  - {s}")
    lines.append(
        "  → Multiple vitals diverged from baseline simultaneously. Two plausible "
        "drivers: illness (immune response) or severe training stress (hard session "
        "+ under-recovery)."
    )
    if severity == "HIGH":
        lines.append(
            "  → HIGH severity: training today MUST be rest/recovery regardless of "
            "DECISION or muscle freshness. If no hard session in last 48h, consider early illness."
        )
    else:
        lines.append(
            "  → MODERATE: train lighter than DECISION suggests, or swap for recovery work. "
            "If it persists another day, treat as HIGH."
        )
    return "\n".join(lines)


def professional_coach_layer(db: Database, metrics: dict | None = None, target_date: date | None = None) -> str:
    """Sports-science context layer: evidence, objectives, and guardrails for the LLM coach."""
    current_metrics = metrics
    if current_metrics is None:
        recent_metrics = db.get_recent_metrics(days=1)
        current_metrics = recent_metrics[0] if recent_metrics else None
    if current_metrics is None:
        return ""

    fatigue = _compute_muscle_group_fatigue(db)
    fresh_priority_groups = [
        group for group in PRIORITY_STRENGTH_GROUPS
        if fatigue.get(group, 0) < 35
    ]
    fatigued_groups = sorted([group for group, value in fatigue.items() if value >= 50])

    sleep_min = current_metrics.get("sleep_duration_min") or 0
    sleep_h = sleep_min / 60 if sleep_min else 0
    sleep_debt_min = _sleep_debt_minutes(db)

    hrv = current_metrics.get("hrv_last_night") or 0
    hrv_baseline = current_metrics.get("hrv_weekly_avg") or hrv
    hrv_delta_pct = ((hrv - hrv_baseline) / hrv_baseline * 100) if hrv_baseline > 0 else 0
    bb_wake = (
        current_metrics.get("bb_at_wake")
        or current_metrics.get("body_battery_am")
        or 0
    )
    acwr = current_metrics.get("acwr_ratio") or 0
    readiness_level = current_metrics.get("training_readiness_level") or "UNKNOWN"
    period_active = _is_period_active(current_metrics)
    strain_severity, _strain_signals = systemic_strain_check(db, current_metrics)
    planned_session = _planned_session_for_date(target_date)
    planned_type = str((planned_session or {}).get("type") or "").lower()
    planned_recovery = _is_recovery_plan_type(planned_type)
    adaptive_override = _adaptive_plan_override(db, current_metrics, planned_session, target_date)

    recent_activities = db.get_recent_activities(days=7)
    high_impact_recent = any(
        activity.get("type") in HIGH_IMPACT_ACTIVITY_TYPES
        and date.fromisoformat(activity["date"]) >= date.today() - timedelta(days=2)
        for activity in recent_activities
    )
    strength_sessions = sum(1 for activity in recent_activities if activity.get("type") == "strength")

    recovery_red_flags = []
    if strain_severity == "HIGH":
        recovery_red_flags.append("systemic strain HIGH")
    if sleep_h > 0 and sleep_h < 4:
        recovery_red_flags.append(f"sleep {sleep_h:.1f}h < 4h")
    if bb_wake > 0 and bb_wake < 20:
        recovery_red_flags.append(f"BB at wake {bb_wake} < 20")
    if hrv_delta_pct < -15:
        recovery_red_flags.append(f"HRV {hrv_delta_pct:+.0f}% vs baseline")

    load_constraints = []
    if sleep_debt_min >= 300:
        load_constraints.append(f"sleep debt {sleep_debt_min/60:.1f}h")
    if hrv_delta_pct < 0:
        load_constraints.append(f"HRV {hrv_delta_pct:+.0f}% vs baseline")
    if bb_wake > 0 and bb_wake < 40:
        load_constraints.append(f"BB at wake {bb_wake}")
    if acwr > 1.1:
        load_constraints.append(f"ACWR {acwr:.2f} elevated")
    if high_impact_recent:
        load_constraints.append("recent high-impact sport")
    if planned_type == "basketball":
        load_constraints.append("planned basketball today")

    if recovery_red_flags:
        training_intent = "Recovery-only constraint: protect adaptation and avoid strength loading."
        planning_objective = "Select the lowest-stress recovery modality that still supports consistency."
    elif planned_type == "basketball":
        training_intent = "Basketball-first constraint: protect evening play; do not stack a separate gym session."
        planning_objective = "Generate a basketball-first plan with warmup/support work only if it improves readiness."
    elif planned_recovery:
        training_intent = "Planned recovery/skill constraint: honor the weekly plan's recovery gate instead of filling the day with strength."
        planning_objective = "Choose recovery or skill work that preserves the rotating microcycle for the next eligible training slot."
    elif adaptive_override is not None:
        training_intent = (
            "Adaptive recovery/skill constraint: defer the planned strength slot because the last actual "
            "session already loaded the same priority tissues."
        )
        planning_objective = "Choose the best recovery/skill option from the eligible modalities; do not copy this contract as final text."
    elif acwr < 0.8 and readiness_level == "HIGH":
        training_intent = "Rebuild consistency: add quality strength plus easy aerobic base without a load spike."
        planning_objective = "Design a conservative build day that improves rhythm without creating a load spike."
    elif load_constraints:
        training_intent = "Controlled build: train useful patterns, but do not chase PRs or fatigue."
        planning_objective = "Design a useful but capped session; make the cap obvious in user-facing language."
    else:
        training_intent = "Build day: progress the next priority pattern while keeping technique clean."
        planning_objective = "Design the best professional plan for today from the evidence, goals, and constraints."

    if recovery_red_flags:
        target_groups = []
        movement_patterns = [f"recovery menu ({HOME_MICRO_SESSION}; easy bike; gentle gym)"]
    elif planned_type == "basketball":
        target_groups = []
        movement_patterns = [f"pre-basketball prep ({HOME_MICRO_SESSION}; light shooting warm-up if desired)"]
    elif planned_recovery:
        target_groups = []
        movement_patterns = [
            "planned recovery/skill menu (easy walk, swim technique, breathing/core-control micro work)",
            "save lower-balance strength for the next eligible slot",
        ]
    elif adaptive_override is not None:
        target_groups = []
        movement_patterns = [
            "swim technique or easy Zone 1-2 cardio",
            f"core-control micro work ({HOME_MICRO_SESSION})",
            "optional non-overlap gym technique only",
        ]
    else:
        target_groups = fresh_priority_groups or [
            group for group in PRIORITY_STRENGTH_GROUPS if group not in fatigued_groups
        ]
        if not target_groups:
            target_groups = ["back", "core"]
        movement_patterns = [
            MOVEMENT_PATTERN_BY_GROUP[group]
            for group in target_groups
            if group in MOVEMENT_PATTERN_BY_GROUP
        ]

    hard_constraints = []
    if period_active:
        hard_constraints.append("No swim while period is active; aerobic substitute = mobility, easy bike, or gentle gym.")
    if recovery_red_flags:
        hard_constraints.append("No heavy lifting or HIIT until red flags clear.")
    if adaptive_override is not None:
        hard_constraints.append(
            "Do not repeat overlapping strength groups from the last session: "
            f"{', '.join(adaptive_override['overlapping_groups'])}."
        )
        hard_constraints.append("Weekly plan is a baseline; actual completed training plus recovery/load gates override it.")
    if planned_recovery:
        hard_constraints.append("Honor the planned recovery/skill day; do not turn it into a strength catch-up session.")
    if high_impact_recent:
        hard_constraints.append("Avoid hard tennis, plyometrics, and heavy quad volume after recent high-impact load.")
    if planned_type == "basketball":
        hard_constraints.append("No rescue lifting or stacked hard conditioning before basketball; avoid heavy hinges, heavy quad volume, HIIT, and PR attempts.")
    if "quads" in fatigued_groups or high_impact_recent:
        hard_constraints.append("Quad work is maintenance-only after recent high-impact load or quad fatigue; otherwise use controlled quad/single-leg exposure as part of lower-balance programming.")
    hard_constraints.append("Sharp joint pain, dizziness, chest pain, or unusual shortness of breath = stop and downgrade.")

    lines = [
        "## Professional Sports Science Context (computed — facts and guardrails, not a template)",
        f"Coach role: strength & conditioning programming for body recomp + athletic transfer.",
        f"Weekly status: {strength_sessions}/2 strength sessions done; target is consistency, not random daily workouts.",
        "Programming style: use a rotating weekly microcycle, not the same exercise checklist; choose movement patterns from the user's long-term plan and current constraints.",
    ]
    if planned_session is not None:
        prescription = str(planned_session.get("prescription") or "").strip().replace("\n", " ")
        if prescription:
            lines.append(f"Today's baseline plan: {planned_type or 'unknown'} — {prescription}")
        else:
            lines.append(f"Today's baseline plan: {planned_type or 'unknown'}.")
    lines.extend([
        f"Decision frame: {training_intent}",
        f"Coach objective: {planning_objective}",
    ])

    if load_constraints:
        lines.append(f"Load modifiers: {', '.join(load_constraints)}.")
    if adaptive_override is not None:
        latest = adaptive_override["latest"]
        lines.append(
            "Adaptive override evidence: "
            f"Trigger: {', '.join(adaptive_override['reasons'])}; "
            f"last actual strength on {latest['date']} emphasized {', '.join(latest['groups'])}; "
            f"planned {adaptive_override['planned_type']} overlaps {', '.join(adaptive_override['overlapping_groups'])}."
        )
        lines.append(f"Eligible modalities: {'; '.join(adaptive_override['eligible_modalities'])}.")
    if recovery_red_flags:
        lines.append(f"Red flags: {', '.join(recovery_red_flags)}.")
    if target_groups:
        lines.append(f"Priority body focus: {', '.join(target_groups)}.")
    if movement_patterns:
        lines.append(f"Eligible movement patterns: {'; '.join(movement_patterns)}.")

    lines.extend([
        "Progression rule: use last successful working weights; if sleep debt, HRV trend, period symptoms, or soreness are present, keep the same load or reduce 5-10%. Add reps/quality before adding weight.",
        "Sport-transfer logic: posterior chain + glutes support basketball first step, hiking climbs, and snow/sport deceleration; upper back/shoulders offset desk posture and support pulling mechanics; controlled quad/single-leg work supports balanced athleticism when recovery allows.",
        f"Hard constraints: {' '.join(hard_constraints)}",
        "Feedback loop: after the session, capture completed exercises, loads, RPE, and any pain/symptom note; the next plan should adjust from actual completion, not from the planned workout.",
    ])

    return "\n".join(lines)


def _normalized_exercise_name(exercise: object) -> str:
    return str(exercise or "").strip().lower().replace(" ", "_").replace("-", "_")


def _exercise_group_map(exercise: object) -> dict[str, float]:
    normalized = _normalized_exercise_name(exercise)
    for key, group_map in _MUSCLE_MAP_BY_EXERCISE.items():
        if key in normalized:
            return group_map
    return {}


def post_session_feedback_loop(db: Database) -> str:
    """Turn the latest completed strength session into next-session programming rules."""
    strength_activities = db.get_recent_activities(days=30, activity_type="strength")
    if not strength_activities:
        return "\n".join([
            "## Post-Session Feedback Loop (computed — LLM MUST use this)",
            "No recent strength session found. Start with conservative loads and treat the next session as baseline collection.",
            "Feedback needed after next session: completed exercises, loads, RPE, and any pain/symptom note.",
        ])

    latest = strength_activities[0]
    sets = db.get_gym_sets(latest["id"])
    feedback = db.get_training_feedback(latest["id"])
    if not sets:
        return "\n".join([
            "## Post-Session Feedback Loop (computed — LLM MUST use this)",
            f"Last strength session: {latest.get('date')} ({latest.get('duration_min', '?')} min), but no set detail was captured.",
            "Next-session rule: keep the plan conservative until exercise-level completion is available.",
            "Feedback needed: completed exercises, loads, RPE, and any pain/symptom note.",
        ])

    ignored_keywords = ("unknown", "treadmill", "cardio")
    strength_sets = [
        set_row for set_row in sets
        if not any(keyword in _normalized_exercise_name(set_row.get("exercise")) for keyword in ignored_keywords)
        and (set_row.get("reps") or 0) > 0
    ]
    set_count = len(strength_sets)
    rep_count = sum(set_row.get("reps") or 0 for set_row in strength_sets)
    exercises = sorted({
        str(set_row.get("exercise"))
        for set_row in strength_sets
        if set_row.get("exercise")
    })
    recorded_weights = [
        set_row.get("weight_lb")
        for set_row in strength_sets
        if set_row.get("weight_lb") is not None and set_row.get("weight_lb") > 0
    ]
    weight_capture_rate = len(recorded_weights) / set_count if set_count > 0 else 0

    group_load: dict[str, float] = {}
    unmapped_exercises = set()
    for set_row in strength_sets:
        group_map = _exercise_group_map(set_row.get("exercise"))
        if not group_map:
            if set_row.get("exercise"):
                unmapped_exercises.add(str(set_row.get("exercise")))
            continue
        for group, share in group_map.items():
            group_load[group] = group_load.get(group, 0) + share

    top_groups = [
        group for group, _value in sorted(group_load.items(), key=lambda item: item[1], reverse=True)[:5]
    ]

    duration_min = latest.get("duration_min") or 0
    training_load = latest.get("training_load") or 0
    z4_sec = latest.get("hr_zone4_sec") or 0
    z5_sec = latest.get("hr_zone5_sec") or 0
    hard_hr_min = (z4_sec + z5_sec) / 60

    volume_flags = []
    if duration_min >= 75:
        volume_flags.append(f"{duration_min:.0f} min session")
    if set_count >= 20:
        volume_flags.append(f"{set_count} strength sets")
    if training_load >= 100:
        volume_flags.append(f"load {training_load:.0f}")
    if hard_hr_min >= 8:
        volume_flags.append(f"{hard_hr_min:.0f} min Z4/Z5")

    if volume_flags:
        progression_decision = "Do not chase progression next strength session; consolidate technique and keep volume capped."
        next_adjustment = "Next strength dose: 30-40 min, 4-5 movements, 2-3 work sets each, stop 2-3 reps in reserve."
    elif weight_capture_rate < 0.5:
        progression_decision = "Load progression is not auditable because weights are mostly missing; progress by clean reps/RPE only."
        next_adjustment = "Repeat the same main patterns and record weights or RPE before increasing load."
    else:
        progression_decision = "Progress only exercises completed cleanly at target RPE; add 1-2 reps before adding weight."
        next_adjustment = "Keep the same movement pattern and add a small progression only where completion quality was strong."

    if feedback is not None:
        pain_level = feedback.get("pain_level")
        rpe = feedback.get("rpe")
        if pain_level is not None and pain_level >= 4:
            progression_decision = "Pain feedback overrides progression; deload the affected pattern and use pain-free alternatives."
            next_adjustment = "Next strength dose: keep intensity easy, avoid the painful pattern, and prioritize mobility/stability."
        elif rpe is not None and rpe >= 8:
            progression_decision = "High RPE feedback: hold load steady next session and progress only if reps are cleaner."
            next_adjustment = "Next strength dose: repeat main patterns, cap at RPE 7, and stop before form breaks."

    lines = [
        "## Post-Session Feedback Loop (computed — LLM MUST use this)",
        f"Last strength session: {latest.get('date')} | {duration_min:.0f} min | {set_count} strength sets | {rep_count} reps | load {training_load:.0f}.",
    ]
    if top_groups:
        lines.append(f"Actual emphasis: {', '.join(top_groups)}.")
    lines.append(
        f"Weight capture: {len(recorded_weights)}/{set_count} sets with load recorded "
        f"({weight_capture_rate*100:.0f}%)."
    )
    if volume_flags:
        lines.append(f"Volume/strain flags: {', '.join(volume_flags)}.")
    if feedback is not None:
        feedback_bits = []
        if feedback.get("rpe") is not None:
            feedback_bits.append(f"RPE {feedback.get('rpe')}")
        if feedback.get("pain_level") is not None:
            area = f" {feedback.get('pain_area')}" if feedback.get("pain_area") else ""
            feedback_bits.append(f"pain{area} {feedback.get('pain_level')}/10")
        if feedback.get("menstrual_symptoms"):
            feedback_bits.append(f"cycle symptoms: {feedback.get('menstrual_symptoms')}")
        if feedback.get("notes"):
            feedback_bits.append(f"notes: {feedback.get('notes')}")
        lines.append(f"User feedback captured: {'; '.join(feedback_bits) if feedback_bits else 'yes'}.")
    lines.append(f"Progression decision: {progression_decision}")
    lines.append(f"Next-session adjustment: {next_adjustment}")
    if unmapped_exercises:
        lines.append(f"Mapping gap: review these exercises for muscle-group mapping: {', '.join(sorted(unmapped_exercises)[:6])}.")
    lines.append("Feedback request after next workout: ask for actual RPE and any ankle/knee/low-back pain if Garmin lacks it.")

    return "\n".join(lines)


def weekly_programming_layer(db: Database) -> str:
    """One-week S&C programming frame: targets, current gaps, and recovery budget."""
    activities = db.get_recent_activities(days=7)
    metrics = db.get_daily_metrics()
    period_active = _is_period_active(metrics)
    types_done = [activity.get("type") for activity in activities]
    strength_count = types_done.count("strength")
    swim_count = types_done.count("swimming")
    basketball_count = types_done.count("basketball")
    outdoor_count = sum(1 for activity_type in types_done if activity_type in OUTDOOR_SLOT_ACTIVITY_TYPES)
    total_load = sum(activity.get("training_load") or 0 for activity in activities)

    next_priorities = []
    if strength_count < 2:
        next_priorities.append(f"strength {strength_count}/2")
    if swim_count < 1:
        next_priorities.append("aerobic base deferred to mobility/easy bike/gentle gym while period active" if period_active else "swim/aerobic base 0/1")
    if outdoor_count < 1:
        next_priorities.append("outdoor/adventure optional 0/1")

    if not next_priorities:
        next_priorities.append("weekly minimums covered; bias recovery or skill quality")

    lines = [
        "## Weekly Programming Layer (computed — LLM MUST use this)",
        f"7-day load: {total_load:.0f} | sessions: {len(activities)} | strength {strength_count}/2 | swim {swim_count}/1 | basketball {basketball_count} | outdoor {outdoor_count}/1 optional.",
        f"Next priorities: {', '.join(next_priorities)}.",
        "Programming rule: protect 2 quality strength exposures per week using rotating emphases (upper pull/shoulder, lower balance with posterior chain plus controlled quad, optional athletic micro); add aerobic/base work only when it does not compromise recovery or period constraints.",
        "Deload trigger: if red flags, pain feedback, or high-volume strength appear, preserve frequency but reduce volume/intensity before adding sessions.",
    ]

    return "\n".join(lines)


def exercise_progression_layer(db: Database) -> str:
    """Exercise-level progression rules from recent set history."""
    strength_activities = db.get_recent_activities(days=45, activity_type="strength")
    if not strength_activities:
        return "\n".join([
            "## Exercise Progression Layer (computed — LLM MUST use this)",
            "No recent strength history. Treat the next session as baseline collection.",
        ])

    exercise_sets: dict[str, list[dict[str, Any]]] = {}
    for activity in strength_activities:
        for set_row in db.get_gym_sets(activity["id"]):
            exercise = str(set_row.get("exercise") or "").strip()
            reps = set_row.get("reps")
            if exercise == "" or reps is None or reps <= 0:
                continue
            exercise_sets.setdefault(exercise, []).append({
                "date": activity.get("date"),
                "reps": reps,
                "weight_lb": set_row.get("weight_lb"),
                "source": set_row.get("source") or "garmin",
            })

    priority_exercises = [
        "Lat Pulldown",
        "Seated Cable Row",
        "Romanian Deadlift",
        "Barbell Hip Thrust On Floor",
        "Face Pull",
        "Lateral Raise",
        "Leg Curl",
        "Leg Press",
    ]

    lines = ["## Exercise Progression Layer (computed — LLM MUST use this)"]
    for exercise in priority_exercises:
        history = exercise_sets.get(exercise, [])
        if not history:
            continue
        weighted = [
            set_row for set_row in history
            if set_row.get("weight_lb") is not None and set_row.get("weight_lb") > 0
        ]
        recent_reps = [set_row.get("reps") for set_row in history[:3] if set_row.get("reps") is not None]
        if len(weighted) < 2:
            lines.append(
                f"- {exercise}: progression not auditable yet ({len(weighted)}/{len(history)} sets have load). Repeat clean reps and capture weight/RPE."
            )
            continue
        latest = weighted[0]
        older = weighted[-1]
        weight_delta = latest["weight_lb"] - older["weight_lb"]
        rep_text = f", recent reps {recent_reps}" if recent_reps else ""
        if weight_delta > 0:
            lines.append(f"- {exercise}: load trending up {older['weight_lb']}→{latest['weight_lb']}lb{rep_text}; hold if RPE >=8, otherwise add reps before another load jump.")
        elif weight_delta < 0:
            lines.append(f"- {exercise}: load reduced {older['weight_lb']}→{latest['weight_lb']}lb{rep_text}; rebuild with clean reps before adding weight.")
        else:
            lines.append(f"- {exercise}: stable at {latest['weight_lb']}lb{rep_text}; add 1-2 reps first, then a small load increase if quality is solid.")

    if len(lines) == 1:
        lines.append("Recent exercises exist, but none of the priority movements have auditable load history yet. Use conservative repeat work and capture loads/RPE.")

    return "\n".join(lines)


def daily_summary(db: Database, metrics: dict | None = None) -> str:
    parts = [
        decision_logic(db, metrics) if metrics else None,
        systemic_strain_block(db, metrics) if metrics else None,
        menstrual_constraint(db, metrics),
        professional_coach_layer(db, metrics),
        post_session_feedback_loop(db),
        weekly_programming_layer(db),
        exercise_progression_layer(db),
        readiness_attribution(db),
        recovery_insights(db),
        sleep_quality_insights(db),
        bb_dynamics_insights(db),
        load_with_corrections(db),
        training_accountability(db),
        recent_activity_detail(db, hours=72),
        muscle_group_fatigue(db),
        training_intensity_trend(db),
        weekly_gap_analysis(db),
        concerns_summary(db),
    ]

    activities = db.get_recent_activities(days=7)
    if activities:
        types = [a["type"] for a in activities]
        parts.append(f"Last 7 days: {len(activities)} activities ({', '.join(set(types))})")

    # ski_briefing = pre_ski_briefing(db)  # paused post-season
    # if ski_briefing:
    #     parts.append(ski_briefing)

    # Ski season paused 2026-04-13 (ended 4/12). Re-enable next Nov.
    # ski = db.get_recent_activities(days=30, activity_type="skiing")
    # if ski:
    #     parts.append(ski_insights(db))

    gym = db.get_recent_activities(days=30, activity_type="strength")
    if gym:
        parts.append(gym_insights(db))

    return "\n\n".join(p for p in parts if p)
"""
New insights for Tier 3 digest enhancements.
Appended to src/ai/insights.py.
"""

# ─────────────────────────────────────────────────────────────
# SESSION INTENT CLASSIFIER
# ─────────────────────────────────────────────────────────────

def _session_intent(activity: dict) -> tuple[str, str]:
    """
    Classify a session as easy / moderate / tempo / intervals / competitive / strength / unknown.
    Returns (label, one-line explanation).
    """
    z1 = activity.get("hr_zone1_sec") or 0
    z2 = activity.get("hr_zone2_sec") or 0
    z3 = activity.get("hr_zone3_sec") or 0
    z4 = activity.get("hr_zone4_sec") or 0
    z5 = activity.get("hr_zone5_sec") or 0
    total = z1 + z2 + z3 + z4 + z5
    type_ = (activity.get("type") or "").lower()
    max_hr = activity.get("max_hr") or 0
    label = activity.get("training_effect_label") or ""

    if type_ in ("strength", "strength_training", "gym"):
        return ("strength", "strength work")

    if type_ == "basketball":
        return ("competitive", "basketball game (full-body, high anaerobic)")

    if total == 0:
        return ("unknown", "no HR zone data")

    pct_easy = (z1 + z2) / total
    pct_hard = (z4 + z5) / total
    pct_z5 = z5 / total

    if pct_z5 >= 0.08 and max_hr >= 170:
        return ("intervals", f"{pct_z5*100:.0f}% in Z5, max HR {max_hr}")
    if pct_hard >= 0.30:
        return ("tempo", f"{pct_hard*100:.0f}% in Z4+Z5")
    if pct_easy >= 0.70:
        return ("easy", f"{pct_easy*100:.0f}% in Z1-Z2")
    return ("moderate", f"{pct_easy*100:.0f}% Z1-Z2 / {pct_hard*100:.0f}% Z4-Z5")


def _estimated_recovery_hours(training_load: float, anaerobic_te: float, intent: str) -> int:
    """
    Heuristic recovery time in hours, based on Garmin's TE-based bands:
      TE < 2.5: 12-24h (low-med)
      TE 2.5-3.5: 24-36h (high)
      TE 3.5-4.5: 36-60h (very high)
      TE > 4.5: 60-96h (overreaching)
    Capped at 96h (4 days) — longer implies structural issue, not session.
    """
    if training_load is None or training_load == 0:
        return 0
    # Use max TE as the primary recovery driver
    te = max(anaerobic_te or 0, 0)
    if te >= 4.5:
        base = 72
    elif te >= 3.5:
        base = 48
    elif te >= 2.5:
        base = 30
    elif te > 0:
        base = 18
    else:
        base = max(12, min(36, training_load * 0.08))
    multiplier = {
        "intervals": 1.2,
        "tempo": 1.1,
        "competitive": 1.2,  # slight bump for full-body game stress
        "strength": 1.15,
        "moderate": 1.0,
        "easy": 0.7,
        "unknown": 1.0,
    }.get(intent, 1.0)
    return int(round(min(96, base * multiplier)))


# ─────────────────────────────────────────────────────────────
# RECENT ACTIVITY DETAIL (last 48h)
# ─────────────────────────────────────────────────────────────

def recent_activity_detail(db: Database, hours: int = 48) -> str:
    """Per-session detail for the last N hours: HR zones, TE, intent, recovery state."""
    from datetime import datetime, timezone

    activities = db.get_recent_activities(days=3)
    now = datetime.now()

    recent = []
    for a in activities:
        st = a.get("start_time")
        if not st:
            continue
        try:
            dt = datetime.fromisoformat(st.replace("Z", "").split("+")[0])
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(st[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        hours_ago = (now - dt).total_seconds() / 3600
        if hours_ago <= hours:
            recent.append((a, dt, hours_ago))

    if not recent:
        return f"## Recent Activity Detail (last {hours}h)\nNo workouts in the last {hours}h."

    lines = [f"## Recent Activity Detail (last {hours}h)"]

    total_residual = 0.0
    for a, dt, hrs_ago in recent:
        name = a.get("activity_name") or a.get("type") or "workout"
        type_ = a.get("type") or ""
        dur_min = a.get("duration_min") or 0
        dist_km = (a.get("distance_m") or 0) / 1000
        elev_gain = a.get("elevation_gain") or 0
        avg_hr = a.get("avg_hr") or 0
        max_hr = a.get("max_hr") or 0
        cal = a.get("calories") or 0
        load = a.get("training_load") or 0
        te_aer = a.get("aerobic_te") or 0
        te_ana = a.get("anaerobic_te") or 0
        te_label = a.get("training_effect_label") or ""

        intent, intent_why = _session_intent(a)
        from .adopted import recovery_modifiers
        personal_modifier = recovery_modifiers(db).get(str(type_), 1.0)
        needed_hrs = round(_estimated_recovery_hours(load, te_ana, intent) * personal_modifier)
        recovered_pct = min(100, int(round((hrs_ago / needed_hrs) * 100))) if needed_hrs > 0 else 100
        residual_load = load * max(0, 1 - (hrs_ago / needed_hrs)) if needed_hrs > 0 else 0
        total_residual += residual_load

        z1 = (a.get("hr_zone1_sec") or 0) // 60
        z2 = (a.get("hr_zone2_sec") or 0) // 60
        z3 = (a.get("hr_zone3_sec") or 0) // 60
        z4 = (a.get("hr_zone4_sec") or 0) // 60
        z5 = (a.get("hr_zone5_sec") or 0) // 60

        block = [
            f"\n{a.get('date')} {dt.strftime('%H:%M')} — {name} ({type_})",
            f"  Duration: {dur_min:.0f}m | Distance: {dist_km:.1f}km | Elev gain: {elev_gain:.0f}m",
            f"  Avg HR: {avg_hr} | Max HR: {max_hr} | Calories: {cal}",
            f"  HR zones (min): Z1 {z1} | Z2 {z2} | Z3 {z3} | Z4 {z4} | Z5 {z5}",
            f"  Training load: {load:.0f} | TE aerobic: {te_aer:.1f} | TE anaerobic: {te_ana:.1f} | {te_label}",
            f"  Intent: {intent.upper()} ({intent_why})",
            f"  Recovery: {hrs_ago:.1f}h elapsed / {needed_hrs}h needed = {recovered_pct}% recovered"
            + (f" (personalized x{personal_modifier} from adopted insight)" if personal_modifier != 1.0 else ""),
        ]
        lines.extend(block)

    lines.append(f"\nTotal residual load from last {hours}h: {total_residual:.0f}")
    if total_residual > 100:
        lines.append("  ⚠️ Significant residual fatigue — factor into today's intensity.")
    elif total_residual < 30:
        lines.append("  ✓ Most recent training recovered; today is relatively fresh.")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# MUSCLE GROUP FATIGUE TRACKER
# ─────────────────────────────────────────────────────────────

# Map activity type → muscle group load multipliers (sum need not = 1)
# Values are share of session load attributed to each group.
_MUSCLE_MAP_BY_TYPE = {
    "hiking":        {"quads": 0.35, "calves": 0.25, "glutes": 0.20, "posterior_chain": 0.20, "core": 0.15},
    "running":       {"quads": 0.30, "calves": 0.25, "glutes": 0.15, "posterior_chain": 0.20, "core": 0.15},
    "basketball":    {"quads": 0.40, "calves": 0.25, "glutes": 0.20, "core": 0.20, "shoulders": 0.10},
    "skiing":        {"quads": 0.45, "calves": 0.20, "core": 0.25, "posterior_chain": 0.10},
    "snowboarding":  {"quads": 0.40, "calves": 0.20, "core": 0.25, "posterior_chain": 0.10},
    "swimming":      {"shoulders": 0.35, "back": 0.30, "core": 0.25, "chest": 0.15, "posterior_chain": 0.10},
    "cycling":       {"quads": 0.40, "glutes": 0.20, "calves": 0.15, "core": 0.15},
    "tennis":        {"quads": 0.25, "core": 0.25, "shoulders": 0.25, "back": 0.15},
    "strength":      {"full_body": 1.0},  # resolved from gym_sets when available
}

# Map gym exercises → primary muscle groups (used when strength session has sets data)
_MUSCLE_MAP_BY_EXERCISE = {
    # lower
    "squat": {"quads": 0.6, "glutes": 0.3, "core": 0.1},
    "goblet_squat": {"quads": 0.6, "glutes": 0.3, "core": 0.1},
    "leg_press": {"quads": 0.7, "glutes": 0.3},
    "rdl": {"posterior_chain": 0.7, "glutes": 0.3},
    "romanian_deadlift": {"posterior_chain": 0.7, "glutes": 0.3},
    "hip_thrust": {"glutes": 0.7, "posterior_chain": 0.3},
    "hip_raise": {"glutes": 0.7, "posterior_chain": 0.3},
    "leg_curl": {"posterior_chain": 1.0},
    "leg_extension": {"quads": 1.0},
    "lunge": {"quads": 0.5, "glutes": 0.3, "posterior_chain": 0.2},
    # pull
    "row": {"back": 0.7, "shoulders": 0.2, "core": 0.1},
    "cable_row": {"back": 0.7, "shoulders": 0.2, "core": 0.1},
    "seated_row": {"back": 0.7, "shoulders": 0.2, "core": 0.1},
    "pulldown": {"back": 0.8, "shoulders": 0.2},
    "lat_pulldown": {"back": 0.8, "shoulders": 0.2},
    "pullup": {"back": 0.8, "shoulders": 0.2},
    "face_pull": {"shoulders": 0.6, "back": 0.4},
    # push
    "bench": {"chest": 0.6, "shoulders": 0.3, "core": 0.1},
    "bench_press": {"chest": 0.6, "shoulders": 0.3, "core": 0.1},
    "shoulder_press": {"shoulders": 0.8, "core": 0.2},
    "overhead_press": {"shoulders": 0.8, "core": 0.2},
    "lateral_raise": {"shoulders": 0.9, "core": 0.1},
    "triceps": {"arms": 1.0},
    # core
    "plank": {"core": 1.0},
    "pallof": {"core": 1.0},
    "ab_crunch": {"core": 1.0},
    "leg_raise": {"core": 1.0},
}


def _decay(load: float, hours_ago: float, tau_hours: float = 48.0) -> float:
    """Exponential decay of muscle-group load with τ hours."""
    import math
    if hours_ago <= 0:
        return load
    return load * math.exp(-hours_ago / tau_hours)


def muscle_group_fatigue(db: Database) -> str:
    """
    Estimate residual fatigue per muscle group from last 7 days of workouts.
    Assumes τ=48h exponential decay.
    """
    from datetime import datetime

    activities = db.get_recent_activities(days=7)
    now = datetime.now()
    residual: dict[str, float] = {}

    for a in activities:
        type_ = (a.get("type") or "").lower()
        load = a.get("training_load") or 0
        if load == 0:
            continue

        st = a.get("start_time")
        if not st:
            continue
        try:
            dt = datetime.fromisoformat(st.replace("Z", "").split("+")[0])
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(st[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        hours_ago = (now - dt).total_seconds() / 3600
        decayed = _decay(load, hours_ago)
        if decayed < 1:
            continue

        # Distribute load to muscle groups
        group_map = _MUSCLE_MAP_BY_TYPE.get(type_)
        if type_ in ("strength", "strength_training", "gym"):
            # Try to resolve from gym_sets
            try:
                sets = db.get_gym_sets(a["id"]) if hasattr(db, "get_gym_sets") else []
            except Exception:
                sets = []
            if sets:
                for s in sets:
                    ex = (s.get("exercise") or "").lower().replace(" ", "_").replace("-", "_")
                    ex_map = None
                    for key, m in _MUSCLE_MAP_BY_EXERCISE.items():
                        if key in ex:
                            ex_map = m
                            break
                    if ex_map:
                        weight = 1.0 / len(sets)  # split load evenly across sets
                        for mg, pct in ex_map.items():
                            residual[mg] = residual.get(mg, 0) + decayed * weight * pct
                continue
            group_map = {"full_body": 1.0}

        if not group_map:
            continue
        for mg, pct in group_map.items():
            residual[mg] = residual.get(mg, 0) + decayed * pct

    if not residual:
        return "## Muscle Group Fatigue (computed, 48h decay)\nNo recent workouts to analyze."

    lines = ["## Muscle Group Fatigue (computed, 48h exponential decay)"]
    # Sort by fatigue descending
    sorted_groups = sorted(residual.items(), key=lambda x: -x[1])
    for mg, val in sorted_groups:
        state = "FRESH" if val < 20 else "MODERATE" if val < 50 else "FATIGUED" if val < 100 else "OVERLOADED"
        bar = "█" * min(20, int(val / 10))
        lines.append(f"  {mg:18s} {val:5.0f} {bar} {state}")

    # Readiness hints
    fresh_groups = [g for g, v in sorted_groups if v < 20]
    fatigued_groups = [g for g, v in sorted_groups if v >= 50]
    if fresh_groups:
        lines.append(f"\n  ✓ Ready to train: {', '.join(fresh_groups)}")
    if fatigued_groups:
        lines.append(f"  ⚠️  Needs recovery: {', '.join(fatigued_groups)}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# TRAINING INTENSITY TREND (week-over-week)
# ─────────────────────────────────────────────────────────────

def training_intensity_trend(db: Database) -> str:
    """Compare this week's training intensity distribution to last week and 4-week baseline."""
    from datetime import datetime

    all_28 = db.get_recent_activities(days=28)
    if not all_28:
        return "## Training Intensity Trend (computed)\nNo data."

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    last_week_start = week_start - timedelta(days=7)

    this_week = [a for a in all_28 if date.fromisoformat(a["date"]) >= week_start]
    last_week = [a for a in all_28 if last_week_start <= date.fromisoformat(a["date"]) < week_start]
    last_4w = all_28

    def stats(acts):
        if not acts:
            return None
        loads = [a.get("training_load") or 0 for a in acts]
        hard_time = sum((a.get("hr_zone4_sec") or 0) + (a.get("hr_zone5_sec") or 0) for a in acts) / 60
        easy_time = sum((a.get("hr_zone1_sec") or 0) + (a.get("hr_zone2_sec") or 0) for a in acts) / 60
        intents = []
        for a in acts:
            intent, _ = _session_intent(a)
            intents.append(intent)
        from collections import Counter
        return {
            "count": len(acts),
            "total_load": sum(loads),
            "avg_load": sum(loads) / len(loads) if loads else 0,
            "hard_min": hard_time,
            "easy_min": easy_time,
            "intents": Counter(intents),
        }

    tw = stats(this_week)
    lw = stats(last_week)
    m4 = stats(last_4w)

    lines = ["## Training Intensity Trend (computed)"]

    if tw:
        intents_str = ", ".join(f"{n}×{k}" for k, n in tw["intents"].most_common())
        lines.append(f"This week:  {tw['count']} sessions | total load {tw['total_load']:.0f} | avg {tw['avg_load']:.0f}")
        lines.append(f"            hard (Z4+Z5): {tw['hard_min']:.0f}min | easy (Z1+Z2): {tw['easy_min']:.0f}min")
        lines.append(f"            intents: {intents_str}")

    if lw:
        lines.append(f"Last week:  {lw['count']} sessions | total load {lw['total_load']:.0f} | avg {lw['avg_load']:.0f}")

    if m4 and m4["count"] > 0:
        baseline_avg = m4["total_load"] / 4  # per week
        lines.append(f"4-wk avg:   {baseline_avg:.0f} load/week | {m4['count']/4:.1f} sessions/week")

    # Trend call
    if tw and lw:
        load_delta = tw["total_load"] - lw["total_load"]
        count_delta = tw["count"] - lw["count"]
        pct = (load_delta / lw["total_load"] * 100) if lw["total_load"] > 0 else 0
        if abs(pct) > 25:
            direction = "📈 RISING" if load_delta > 0 else "📉 FALLING"
            lines.append(f"  {direction}: total load {pct:+.0f}% vs last week ({count_delta:+d} sessions)")
        else:
            lines.append(f"  Stable: total load {pct:+.0f}% vs last week")

    # Intensity balance hint
    if tw and tw["hard_min"] > 0:
        pct_hard = tw["hard_min"] / (tw["hard_min"] + tw["easy_min"]) * 100 if (tw["hard_min"] + tw["easy_min"]) > 0 else 0
        if pct_hard > 30:
            lines.append(f"  ⚠️  {pct_hard:.0f}% time in hard zones — may need more easy/aerobic base")
        elif pct_hard < 10:
            lines.append(f"  Heavy on easy — fine unless targeting performance peak")

    return "\n".join(lines)
"""
Decision logic — appended to src/ai/insights.py.
Reuses _MUSCLE_MAP_BY_TYPE, _MUSCLE_MAP_BY_EXERCISE, _decay already defined above.
"""


def _compute_muscle_group_fatigue(db: Database) -> dict[str, float]:
    """Return {muscle_group: residual_load} dict. Same math as muscle_group_fatigue()."""
    from datetime import datetime

    activities = db.get_recent_activities(days=7)
    now = datetime.now()
    residual: dict[str, float] = {}

    for a in activities:
        type_ = (a.get("type") or "").lower()
        load = a.get("training_load") or 0
        if load == 0:
            continue
        st = a.get("start_time")
        if not st:
            continue
        try:
            dt = datetime.fromisoformat(st.replace("Z", "").split("+")[0])
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(st[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        hours_ago = (now - dt).total_seconds() / 3600
        decayed = _decay(load, hours_ago)
        if decayed < 1:
            continue

        group_map = _MUSCLE_MAP_BY_TYPE.get(type_)
        if type_ in ("strength", "strength_training", "gym"):
            try:
                sets = db.get_gym_sets(a["id"]) if hasattr(db, "get_gym_sets") else []
            except Exception:
                sets = []
            if sets:
                for s in sets:
                    ex = (s.get("exercise") or "").lower().replace(" ", "_").replace("-", "_")
                    ex_map = None
                    for key, m in _MUSCLE_MAP_BY_EXERCISE.items():
                        if key in ex:
                            ex_map = m
                            break
                    if ex_map:
                        weight = 1.0 / len(sets)
                        for mg, pct in ex_map.items():
                            residual[mg] = residual.get(mg, 0) + decayed * weight * pct
                continue
            group_map = {"full_body": 1.0}

        if not group_map:
            continue
        for mg, pct in group_map.items():
            residual[mg] = residual.get(mg, 0) + decayed * pct

    return residual


def _hrv_rising_streak(db: Database) -> int:
    """Count consecutive days HRV has been increasing (oldest-to-newest interpretation).
       Returns N where hrv[today] > hrv[today-1] > ... > hrv[today-N]."""
    with db._connection() as conn:
        rows = conn.execute(
            """SELECT date, hrv_last_night FROM daily_metrics
               WHERE date >= date('now', '-7 days') AND hrv_last_night IS NOT NULL
               ORDER BY date DESC"""
        ).fetchall()
    hrvs = [r["hrv_last_night"] for r in rows]
    streak = 0
    for i in range(len(hrvs) - 1):
        if hrvs[i] > hrvs[i + 1]:
            streak += 1
        else:
            break
    return streak


def decision_logic(db: Database, metrics: dict, target_date: date | None = None) -> str:
    """
    Pre-computed decision contract: facts, risk class, and training guardrails.
    The LLM should generate the final coaching plan from this, not copy it.
    """
    fatigue = _compute_muscle_group_fatigue(db)
    fresh_groups = sorted([g for g, v in fatigue.items() if v < 20])
    fatigued_groups = sorted([g for g, v in fatigue.items() if v >= 50])
    all_fatigued = bool(fatigue) and not any(v < 30 for v in fatigue.values())

    hrv = metrics.get("hrv_last_night") or 0
    hrv_baseline = metrics.get("hrv_weekly_avg") or hrv
    hrv_delta_pct = ((hrv - hrv_baseline) / hrv_baseline * 100) if hrv_baseline > 0 else 0
    hrv_rising = _hrv_rising_streak(db)

    sleep_min = metrics.get("sleep_duration_min") or 0
    sleep_h = sleep_min / 60 if sleep_min else 0
    stress = metrics.get("stress_avg") or 0
    bb_wake = metrics.get("bb_at_wake") or metrics.get("body_battery_am") or 0
    acwr = metrics.get("acwr_ratio") or 0
    period_active = _is_period_active(metrics)
    period_day = metrics.get("menstrual_day_of_cycle")
    planned_session = _planned_session_for_date(target_date)
    planned_type = str((planned_session or {}).get("type") or "").lower()
    planned_recovery = _is_recovery_plan_type(planned_type)
    adaptive_override = _adaptive_plan_override(db, metrics, planned_session, target_date)

    # Sleep debt over last 7 recorded sleep nights (target 7h/night). A single
    # good night can hide chronic deficit; missing Garmin sleep data is unknown,
    # not zero sleep.
    sleep_debt_min = _sleep_debt_minutes(db)
    sleep_stressed = sleep_h > 0 and sleep_h < 6 and sleep_debt_min > 300

    raw_level = metrics.get("training_readiness_level") or ""
    effective_level = raw_level
    level_note = ""
    if raw_level == "HIGH" and sleep_stressed:
        effective_level = "MODERATE"
        level_note = (
            f" (downgraded from HIGH — sleep {sleep_h:.1f}h and "
            f"7-day debt {sleep_debt_min/60:.1f}h)"
        )

    strain_severity, strain_signals = systemic_strain_check(db, metrics)

    decision = None
    reason = []

    if strain_severity == "HIGH":
        decision = "SINGLE_REST"
        reason.append(
            f"systemic strain HIGH ({len(strain_signals)} vitals off baseline — see Strain block)"
        )
    elif sleep_h > 0 and sleep_h < 4:
        decision = "SINGLE_REST"
        reason.append(f"sleep {sleep_h:.1f}h < 4h (hard threshold)")
    elif stress > 80:
        decision = "SINGLE_REST"
        reason.append(f"stress_avg {stress} > 80")
    elif bb_wake > 0 and bb_wake < 20:
        decision = "SINGLE_REST"
        reason.append(f"BB at-wake {bb_wake} < 20")
    elif all_fatigued:
        decision = "SINGLE_REST"
        reason.append("all muscle groups FATIGUED/MODERATE — nothing fresh")
    elif hrv_delta_pct < -15:
        decision = "SINGLE_REST"
        reason.append(f"HRV {hrv_delta_pct:+.0f}% vs baseline (deep CNS hit)")
    elif planned_type == "basketball":
        decision = "SCHEDULED_BASKETBALL"
        reason.append("weekly plan schedules basketball today — do not stack rescue lifting")
        if sleep_stressed:
            reason.append(f"sleep debt {sleep_debt_min/60:.1f}h + sleep {sleep_h:.1f}h — cap intensity if energy drops")
        if hrv_delta_pct < 0:
            reason.append(f"HRV {hrv_delta_pct:+.0f}% vs baseline")
    elif planned_recovery:
        decision = "SINGLE_REST"
        reason.append(f"weekly plan schedules {planned_type} today — preserve recovery/skill instead of strength catch-up")
        if acwr > 1.3:
            reason.append(f"ACWR {acwr:.2f} > 1.30")
    elif adaptive_override is not None:
        decision = "SINGLE_REST"
        latest = adaptive_override["latest"]
        reason.append(
            "adaptive recovery override: weekly strength template loses to actual completed training"
        )
        reason.append(
            f"last strength {latest['date']} emphasized {', '.join(latest['groups'])}"
        )
        reason.append(
            f"planned {adaptive_override['planned_type']} overlaps {', '.join(adaptive_override['overlapping_groups'])}"
        )
        reason.extend(adaptive_override["reasons"])
    elif hrv_delta_pct >= 15 and hrv_rising >= 2 and fresh_groups and not sleep_stressed:
        decision = "TWO_OPTION"
        reason.append(f"HRV {hrv_delta_pct:+.0f}% rising {hrv_rising}d — CNS rebound")
        reason.append(f"fresh groups available: {', '.join(fresh_groups)}")
    elif fresh_groups:
        decision = "SINGLE_LIGHT"
        reason.append(f"fresh groups: {', '.join(fresh_groups)}")
        if sleep_stressed:
            reason.append(
                f"sleep debt {sleep_debt_min/60:.1f}h + sleep {sleep_h:.1f}h — "
                f"LIGHT capped (TWO_OPTION blocked even with HRV rebound)"
            )
        else:
            reason.append(f"HRV {hrv_delta_pct:+.0f}% (neutral, not rebounding)")
    else:
        decision = "SINGLE_REST"
        reason.append("no fresh groups + HRV not rebounding")

    lines = [
        "## Today's Training Decision Contract (computed — facts and guardrails, not final copy)",
        f"DECISION: {decision}",
        f"  Effective readiness level: {effective_level}{level_note}",
        f"  Reason: {'; '.join(reason)}",
    ]

    if decision == "TWO_OPTION":
        lines.append(f"  Planning envelope: strength or recovery option may be generated; target fresh groups {', '.join(fresh_groups)}.")
        lines.append(f"  Forbidden/avoid: fatigued groups {', '.join(fatigued_groups) or 'none'}, PR chasing, high fatigue.")
        lines.append("  Safety cap: keep strength conservative and provide a concrete switch-to-recovery condition.")
    elif decision == "SINGLE_LIGHT":
        lines.append(f"  Planning envelope: strength is allowed only for fresh groups {', '.join(fresh_groups)}.")
        lines.append(f"  Forbidden/avoid: fatigued groups {', '.join(fatigued_groups) or 'none'}, PR chasing, high fatigue.")
        lines.append("  Safety cap: keep volume and intensity capped; exact session design is the coach's job.")
    elif decision == "SCHEDULED_BASKETBALL":
        lines.append("  Planning envelope: basketball-first; generate support work only if it improves the basketball session.")
        lines.append("  Forbidden/avoid: separate gym session, rescue lifting, heavy hinge/RDL/hip thrust, HIIT, heavy quad volume, extra conditioning.")
        lines.append("  Safety cap: if warmup/energy is poor, bias skills or shorten runs.")
    else:
        if period_active:
            lines.append("  Planning envelope: recovery-only; choose from mobility, easy bike, or gentle gym.")
            lines.append("  Forbidden/avoid: swim during period, strength work, HIIT, heavy volume.")
        else:
            if adaptive_override is not None or planned_recovery:
                lines.append("  Planning envelope: recovery/skill only; eligible options include swim technique, easy cardio, core-control micro work, or non-overlap gym technique if justified.")
                lines.append("  Forbidden/avoid: planned strength catch-up, overlapping strength groups, PR chasing, HIIT, heavy volume.")
            else:
                lines.append("  Planning envelope: recovery-only; choose a low-load modality that fits the rest of the evidence.")
                lines.append("  Forbidden/avoid: strength work, HIIT, heavy volume.")

    if period_active:
        day_text = f"day {period_day}" if period_day is not None else "day unknown"
        lines.append(f"  Period constraint: active ({day_text}) — DO NOT recommend swim; use mobility/easy bike/gentle gym for aerobic work.")

    lines.append(
        f"\n  Signals used: HRV {hrv} (baseline {hrv_baseline}, {hrv_delta_pct:+.0f}%, rising {hrv_rising}d), "
        f"sleep {sleep_h:.1f}h, BB-wake {bb_wake}, ACWR {acwr}, stress {stress}"
    )

    return "\n".join(lines)
