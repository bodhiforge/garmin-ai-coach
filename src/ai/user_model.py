"""Computed user model — what the system KNOWS about you from data, not what you told it."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from ..db.models import Database


def build_user_model(db: Database) -> str:
    """Build a structured user model from actual data. Not memory file concatenation."""
    sections = []

    sections.append(_training_identity(db))
    sections.append(_physiological_profile(db))
    sections.append(_behavioral_patterns(db))
    sections.append(_progression_trajectory(db))
    sections.append(_blind_spots(db))

    return "\n\n".join(s for s in sections if s)


def _training_identity(db: Database) -> str:
    """Who are you as an athlete — computed from activity distribution."""
    activities = db.get_recent_activities(days=365)
    if not activities:
        return "## Training Identity\nNo data yet."

    type_counts = Counter(a["type"] for a in activities)
    total = len(activities)
    primary_sport = type_counts.most_common(1)[0][0] if type_counts else "unknown"

    # Training frequency
    first_date = date.fromisoformat(activities[-1]["date"])
    weeks = max(1, (date.today() - first_date).days / 7)
    freq = total / weeks

    # Time distribution
    total_min = sum(a.get("duration_min", 0) or 0 for a in activities)
    type_minutes = {}
    for a in activities:
        t = a["type"]
        type_minutes[t] = type_minutes.get(t, 0) + (a.get("duration_min", 0) or 0)

    # Day-of-week pattern
    day_counts = [0] * 7
    for a in activities:
        d = date.fromisoformat(a["date"])
        day_counts[d.weekday()] += 1
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    fav_days = [day_names[i] for i in sorted(range(7), key=lambda i: -day_counts[i])[:3]]
    skip_days = [day_names[i] for i in range(7) if day_counts[i] == 0]

    lines = [
        "## Training Identity",
        f"Primary sport: {primary_sport} ({type_counts[primary_sport]}/{total} sessions)",
        f"Training frequency: {freq:.1f} sessions/week over {weeks:.0f} weeks",
        f"Total tracked time: {total_min/60:.0f} hours across {total} sessions",
    ]

    if len(type_counts) > 1:
        breakdown = ", ".join(f"{t}: {c} ({c/total*100:.0f}%)" for t, c in type_counts.most_common())
        lines.append(f"Sport mix: {breakdown}")

    lines.append(f"Most active days: {', '.join(fav_days)}")
    if skip_days:
        lines.append(f"Never trains on: {', '.join(skip_days)}")

    return "\n".join(lines)


def _physiological_profile(db: Database) -> str:
    """What your body data says — baselines, ranges, patterns."""
    metrics = db.get_recent_metrics(days=90)
    if len(metrics) < 7:
        return ""

    hrvs = [m["hrv_last_night"] for m in metrics if m.get("hrv_last_night")]
    rhrs = [m["resting_hr"] for m in metrics if m.get("resting_hr")]
    sleeps = [m["sleep_duration_min"] for m in metrics if m.get("sleep_duration_min")]
    bbs = [m["body_battery_am"] for m in metrics if m.get("body_battery_am")]

    lines = ["## Physiological Profile"]

    if hrvs:
        avg_hrv = sum(hrvs) / len(hrvs)
        min_hrv = min(hrvs)
        max_hrv = max(hrvs)
        lines.append(f"HRV baseline: {avg_hrv:.0f}ms (range {min_hrv:.0f}–{max_hrv:.0f})")

        # HRV variability — how stable is your nervous system?
        hrv_std = (sum((h - avg_hrv) ** 2 for h in hrvs) / len(hrvs)) ** 0.5
        stability = "stable" if hrv_std < avg_hrv * 0.15 else "variable"
        lines.append(f"HRV stability: {stability} (std dev {hrv_std:.1f}ms)")

    if rhrs:
        avg_rhr = sum(rhrs) / len(rhrs)
        lines.append(f"Resting HR baseline: {avg_rhr:.0f} bpm")

    if sleeps:
        avg_sleep = sum(sleeps) / len(sleeps) / 60
        under_7 = sum(1 for s in sleeps if s < 420)
        lines.append(f"Avg sleep: {avg_sleep:.1f}h ({under_7}/{len(sleeps)} nights under 7h = {under_7/len(sleeps)*100:.0f}%)")

    if bbs:
        avg_bb = sum(bbs) / len(bbs)
        lines.append(f"Avg morning Body Battery: {avg_bb:.0f}/100")

    # Recovery speed — how fast does HRV bounce back after training?
    if hrvs and len(metrics) >= 14:
        activities = db.get_recent_activities(days=90)
        metrics_by_date = {m["date"]: m for m in metrics}
        post_training_drops = []
        for a in activities:
            next_date = str(date.fromisoformat(a["date"]) + timedelta(days=1))
            hrv_day = metrics_by_date.get(a["date"], {}).get("hrv_last_night")
            hrv_next = metrics_by_date.get(next_date, {}).get("hrv_last_night")
            if hrv_day and hrv_next and hrv_day > 0:
                drop = (hrv_next - hrv_day) / hrv_day * 100
                post_training_drops.append(drop)
        if post_training_drops:
            avg_drop = sum(post_training_drops) / len(post_training_drops)
            if avg_drop < -5:
                lines.append(f"Recovery speed: slow (avg {avg_drop:.0f}% HRV drop day after training)")
            elif avg_drop > -2:
                lines.append(f"Recovery speed: fast (avg {avg_drop:.0f}% HRV change day after training)")
            else:
                lines.append(f"Recovery speed: normal (avg {avg_drop:.0f}% HRV change day after training)")

    return "\n".join(lines)


def _behavioral_patterns(db: Database) -> str:
    """How you actually behave — not what you say, what the data shows."""
    activities = db.get_recent_activities(days=90)
    metrics = db.get_recent_metrics(days=90)

    if len(activities) < 5 or len(metrics) < 14:
        return ""

    lines = ["## Behavioral Patterns"]

    # Rest compliance — do you listen to your body?
    low_days = []
    for m in metrics:
        tr = m.get("training_readiness_score")
        if tr is not None and tr < 40:
            low_days.append(m["date"])
        elif m.get("hrv_last_night") and m.get("hrv_last_night") < (sum(
            mm["hrv_last_night"] for mm in metrics if mm.get("hrv_last_night")
        ) / max(1, sum(1 for mm in metrics if mm.get("hrv_last_night")))) * 0.8:
            low_days.append(m["date"])

    if low_days:
        activity_dates = set(a["date"] for a in activities)
        ignored = sum(1 for d in low_days if d in activity_dates)
        if ignored > 0:
            lines.append(f"Rest discipline: ignores low-readiness signals {ignored}/{len(low_days)} times ({ignored/len(low_days)*100:.0f}%)")
        else:
            lines.append("Rest discipline: respects recovery signals consistently")

    # Consistency — are there gaps?
    activity_dates = sorted(set(a["date"] for a in activities))
    gaps = []
    for i in range(1, len(activity_dates)):
        gap = (date.fromisoformat(activity_dates[i]) - date.fromisoformat(activity_dates[i-1])).days
        if gap > 3:
            gaps.append(gap)
    if gaps:
        avg_gap = sum(gaps) / len(gaps)
        lines.append(f"Training gaps: {len(gaps)} gaps of 4+ days (avg {avg_gap:.0f} days)")
    else:
        lines.append("Training gaps: none over 3 days — very consistent")

    # Session length tendency
    durations = [a.get("duration_min", 0) or 0 for a in activities if a.get("duration_min")]
    if durations:
        avg_dur = sum(durations) / len(durations)
        lines.append(f"Avg session length: {avg_dur:.0f} min")

    return "\n".join(lines)


def _progression_trajectory(db: Database) -> str:
    """Where you're headed — trends the user might not see."""
    lines = ["## Progression"]

    # Ski speed trajectory
    ski = db.get_recent_activities(days=365, activity_type="skiing")
    if len(ski) >= 3:
        speeds_by_date = []
        for a in ski:
            runs = db.get_ski_runs(a["id"])
            if runs:
                max_s = max((r.get("max_speed_kmh", 0) or 0 for r in runs), default=0)
                if max_s > 0:
                    speeds_by_date.append((a["date"], max_s))
        speeds_by_date.sort()
        if len(speeds_by_date) >= 3:
            first_3_avg = sum(s for _, s in speeds_by_date[:3]) / 3
            last_3_avg = sum(s for _, s in speeds_by_date[-3:]) / 3
            change = last_3_avg - first_3_avg
            pct = change / first_3_avg * 100 if first_3_avg > 0 else 0

            if abs(pct) < 3:
                lines.append(f"Ski speed: plateaued around {last_3_avg:.1f} km/h")
            elif pct > 0:
                lines.append(f"Ski speed: trending up {pct:+.0f}% ({first_3_avg:.1f} → {last_3_avg:.1f} km/h)")
            else:
                lines.append(f"Ski speed: declining {pct:+.0f}% ({first_3_avg:.1f} → {last_3_avg:.1f} km/h)")

    # Gym weight trajectory
    gym = db.get_recent_activities(days=365, activity_type="strength")
    if len(gym) >= 3:
        exercise_trends = {}
        for a in gym:
            sets = db.get_gym_sets(a["id"])
            for s in (sets or []):
                ex = s.get("exercise", "unknown")
                weight = s.get("weight_kg", 0) or 0
                if weight > 0:
                    if ex not in exercise_trends:
                        exercise_trends[ex] = []
                    exercise_trends[ex].append((a["date"], weight))

        for ex, data in exercise_trends.items():
            if len(data) < 3:
                continue
            data.sort()
            first_w = data[0][1]
            last_w = data[-1][1]
            if first_w > 0:
                change = (last_w - first_w) / first_w * 100
                name = ex.replace("_", " ").title()
                if abs(change) < 3:
                    lines.append(f"{name}: stalled at {last_w:.0f} kg")
                elif change > 0:
                    lines.append(f"{name}: {first_w:.0f} → {last_w:.0f} kg ({change:+.0f}%)")
                else:
                    lines.append(f"{name}: declining {first_w:.0f} → {last_w:.0f} kg ({change:+.0f}%)")

    # HRV trajectory
    metrics = db.get_recent_metrics(days=90)
    hrvs = [(m["date"], m["hrv_last_night"]) for m in metrics if m.get("hrv_last_night")]
    if len(hrvs) >= 14:
        hrvs.sort()
        first_week = [h for _, h in hrvs[:7]]
        last_week = [h for _, h in hrvs[-7:]]
        first_avg = sum(first_week) / len(first_week)
        last_avg = sum(last_week) / len(last_week)
        change = (last_avg - first_avg) / first_avg * 100 if first_avg > 0 else 0
        if abs(change) > 5:
            direction = "improving" if change > 0 else "declining"
            lines.append(f"HRV trend (90d): {direction} {change:+.0f}% ({first_avg:.0f} → {last_avg:.0f}ms)")

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _blind_spots(db: Database) -> str:
    """Things the system noticed that the user probably hasn't."""
    lines = ["## Things You Might Not Know"]
    found_something = False

    metrics = db.get_recent_metrics(days=90)
    activities = db.get_recent_activities(days=90)

    if not metrics or not activities:
        return ""

    metrics_by_date = {m["date"]: m for m in metrics}

    # Sleep → performance correlation
    ski = [a for a in activities if a["type"] == "skiing"]
    if len(ski) >= 4:
        good_sleep_speeds = []
        bad_sleep_speeds = []
        for a in ski:
            prev = str(date.fromisoformat(a["date"]) - timedelta(days=1))
            prev_m = metrics_by_date.get(prev)
            if not prev_m or not prev_m.get("sleep_duration_min"):
                continue
            runs = db.get_ski_runs(a["id"])
            if not runs:
                continue
            max_speed = max((r.get("max_speed_kmh", 0) or 0 for r in runs), default=0)
            if max_speed == 0:
                continue
            if prev_m["sleep_duration_min"] >= 420:
                good_sleep_speeds.append(max_speed)
            else:
                bad_sleep_speeds.append(max_speed)

        if len(good_sleep_speeds) >= 2 and len(bad_sleep_speeds) >= 2:
            avg_good = sum(good_sleep_speeds) / len(good_sleep_speeds)
            avg_bad = sum(bad_sleep_speeds) / len(bad_sleep_speeds)
            if avg_good - avg_bad > 1:
                lines.append(f"Your ski speed is {avg_good:.1f} km/h after 7h+ sleep vs {avg_bad:.1f} km/h after <7h. Sleep is literally making you faster.")
                found_something = True

    # Best time of day
    morning_acts = []
    afternoon_acts = []
    for a in activities:
        # Use start time if available, otherwise skip
        hour = a.get("start_hour")
        if hour is not None:
            if hour < 12:
                morning_acts.append(a)
            else:
                afternoon_acts.append(a)

    # Consecutive day performance
    if len(ski) >= 4:
        activity_dates_set = set(a["date"] for a in activities)
        fresh_speeds = []
        back2back_speeds = []
        for a in ski:
            prev = str(date.fromisoformat(a["date"]) - timedelta(days=1))
            runs = db.get_ski_runs(a["id"])
            if not runs:
                continue
            max_speed = max((r.get("max_speed_kmh", 0) or 0 for r in runs), default=0)
            if max_speed == 0:
                continue
            if prev in activity_dates_set:
                back2back_speeds.append(max_speed)
            else:
                fresh_speeds.append(max_speed)

        if len(fresh_speeds) >= 2 and len(back2back_speeds) >= 2:
            avg_fresh = sum(fresh_speeds) / len(fresh_speeds)
            avg_b2b = sum(back2back_speeds) / len(back2back_speeds)
            diff = avg_fresh - avg_b2b
            if abs(diff) > 1:
                lines.append(f"You're {abs(diff):.1f} km/h {'faster' if diff > 0 else 'slower'} on day 1 vs consecutive days. {'Rest days matter.' if diff > 0 else 'You warm up on day 2.'}")
                found_something = True

    # RHR as stress indicator
    if len(metrics) >= 30:
        rhrs = [(m["date"], m["resting_hr"]) for m in metrics if m.get("resting_hr")]
        if len(rhrs) >= 14:
            rhrs.sort()
            rhr_values = [r for _, r in rhrs]
            avg_rhr = sum(rhr_values) / len(rhr_values)
            spikes = [(d, r) for d, r in rhrs if r > avg_rhr + 5]
            if spikes:
                lines.append(f"RHR spikes (>5bpm above your {avg_rhr:.0f} baseline) on {len(spikes)} days — could indicate stress, illness, or alcohol.")
                found_something = True

    if not found_something:
        return ""
    return "\n".join(lines)
