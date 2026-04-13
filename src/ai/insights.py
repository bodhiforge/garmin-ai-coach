"""Computed insights — Python does the math AND the analysis. LLM only presents."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..db.models import Database


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
            weight = s.get("weight_kg")
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
            lines.append(f"  {ex_name}: {latest['weight']}kg × {latest['reps']} ({latest['date']}) — need more data")
            continue

        first = history[-1]
        last = history[0]
        weight_change = last["weight"] - first["weight"]
        volume_change_pct = ((last["volume"] - first["volume"]) / first["volume"] * 100) if first["volume"] > 0 else 0

        # Plateau detection
        if len(history) >= 3 and all(h["weight"] == history[0]["weight"] for h in history[:3]):
            lines.append(f"  {ex_name}: {last['weight']}kg × {last['reps']} — ⚠️ PLATEAU (same weight 3+ sessions). Increase weight or reps.")
        elif weight_change > 0:
            lines.append(f"  {ex_name}: {first['weight']}→{last['weight']}kg (+{weight_change}kg) | volume {volume_change_pct:+.0f}%")
        else:
            lines.append(f"  {ex_name}: {first['weight']}→{last['weight']}kg ({weight_change:+.0f}kg)")

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
        avg_deep = sum((s.get("sleep_deep_min") or 0) for s in stages) / len(stages)
        avg_total = sum((s.get("sleep_duration_min") or 0) for s in stages) / len(stages)
        avg_deep_pct = round(avg_deep / avg_total * 100) if avg_total > 0 else 0
        avg_bb_charge = sum((s.get("bb_sleep_charge") or 0) for s in stages) / len(stages)

        lines.append(f"7-day avg: deep {avg_deep:.0f}m ({avg_deep_pct}%) | total {avg_total:.0f}m | BB charge {avg_bb_charge:.0f}")

        # Sleep debt
        target_min = 420  # 7 hours
        debt_per_night = [(target_min - (s.get("sleep_duration_min") or 0)) for s in stages]
        total_debt = sum(max(0, d) for d in debt_per_night)
        if total_debt > 120:
            lines.append(f"  ⚠️ Sleep debt: {total_debt:.0f}m ({total_debt/60:.1f}h) accumulated over {len(stages)} days")

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

    # Load balance interpretation
    if balance and "SHORTAGE" in str(balance):
        lines.append(f"  ⚠️ {balance} — add more aerobic work (swim, easy run, cycling)")

    return "\n".join(lines)


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



def weekly_gap_analysis(db) -> str:
    """Detect what's missing from this week's training and suggest what to fill."""
    from datetime import date, timedelta

    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    days_left = 6 - today.weekday()  # remaining days including today

    activities = db.get_recent_activities(days=7)
    this_week = [a for a in activities if date.fromisoformat(a["date"]) >= week_start]

    types_done = [a["type"] for a in this_week]
    dates_done = [a["date"] for a in this_week]

    # What's been done
    has_gym = "strength" in types_done
    has_swim = "swimming" in types_done
    has_ski = "skiing" in types_done
    has_basketball = "basketball" in types_done
    gym_count = types_done.count("strength")
    swim_count = types_done.count("swimming")

    # Weekly targets
    # Basketball: 2x (Wed/Fri, fixed)
    # Ski: 1-2x (weather-dependent, bonus)
    # Gym: 2x (fill available days, priority: back/shoulders + lower/core)
    # Swim: 1x minimum (Costa Rica prep, deadline May 2026)

    lines = ["## Weekly Gap Analysis (computed)"]
    lines.append(f"Done this week: {len(this_week)} sessions ({', '.join(types_done) if types_done else 'none'})")
    lines.append(f"Days left (including today): {days_left + 1}")

    missing = []
    if gym_count < 2:
        needed = 2 - gym_count
        missing.append(f"Gym: need {needed} more (target 2x/week for body recomp)")
    if swim_count < 1:
        missing.append("Swim: need 1 (Costa Rica freestyle prep — 9 weeks out)")

    if missing:
        lines.append("Missing this week:")
        for m in missing:
            lines.append(f"  - {m}")

        # Suggest when to fit them
        # Basketball: Wed/Fri evening → morning is free for gym/swim
        # Ski: unpredictable, but usually takes the whole day
        dow = today.weekday()
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

        suggestions = []
        for offset in range(days_left + 1):
            check_day = today + timedelta(days=offset)
            check_dow = check_day.weekday()
            check_date = check_day.isoformat()

            # Skip if already has activity today
            if check_date in dates_done and offset == 0:
                continue

            if check_dow in (2, 4):  # Wed/Fri — basketball evening
                if gym_count < 2:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: morning gym (basketball is evening)")
                elif swim_count < 1:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: morning swim (basketball is evening)")
            elif check_dow == 6:  # Sunday — rest preferred
                if swim_count < 1:
                    suggestions.append(f"Sun {check_date}: light swim (active recovery)")
            else:  # Mon/Tue/Thu/Sat — open
                if gym_count < 2:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: gym")
                    gym_count += 1
                elif swim_count < 1:
                    suggestions.append(f"{dow_names[check_dow]} {check_date}: swim")
                    swim_count += 1

        if suggestions:
            lines.append("Suggested slots:")
            for s in suggestions[:3]:
                lines.append(f"  → {s}")
    else:
        lines.append("All weekly targets met ✅")

    return "\n".join(lines)


def daily_summary(db: Database) -> str:
    parts = [
        readiness_attribution(db),
        recovery_insights(db),
        sleep_quality_insights(db),
        bb_dynamics_insights(db),
        load_with_corrections(db),
        training_accountability(db),
        recent_activity_detail(db, hours=48),
        muscle_group_fatigue(db),
        training_intensity_trend(db),
        weekly_gap_analysis(db),
        concerns_summary(db),
    ]

    activities = db.get_recent_activities(days=7)
    if activities:
        types = [a["type"] for a in activities]
        parts.append(f"Last 7 days: {len(activities)} activities ({', '.join(set(types))})")

    ski_briefing = pre_ski_briefing(db)
    if ski_briefing:
        parts.append(ski_briefing)

    ski = db.get_recent_activities(days=30, activity_type="skiing")
    if ski:
        parts.append(ski_insights(db))

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
        needed_hrs = _estimated_recovery_hours(load, te_ana, intent)
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
            f"  Recovery: {hrs_ago:.1f}h elapsed / {needed_hrs}h needed = {recovered_pct}% recovered",
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
    # core
    "plank": {"core": 1.0},
    "pallof": {"core": 1.0},
    "ab_crunch": {"core": 1.0},
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
