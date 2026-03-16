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


def daily_summary(db: Database) -> str:
    parts = [recovery_insights(db)]

    activities = db.get_recent_activities(days=7)
    if activities:
        types = [a["type"] for a in activities]
        parts.append(f"Last 7 days: {len(activities)} activities ({', '.join(set(types))})")

    # Pre-ski briefing if consecutive skiing detected
    ski_briefing = pre_ski_briefing(db)
    if ski_briefing:
        parts.append(ski_briefing)

    ski = db.get_recent_activities(days=30, activity_type="skiing")
    if ski:
        parts.append(ski_insights(db))

    gym = db.get_recent_activities(days=30, activity_type="strength")
    if gym:
        parts.append(gym_insights(db))

    return "\n\n".join(parts)
