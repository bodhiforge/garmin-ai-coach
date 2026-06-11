"""Correlation discovery — personal pattern mining with statistical gates.

Python computes; findings below the gate never leave this module. Permutation
tests use a fixed seed: results are deterministic for a given dataset."""
from __future__ import annotations

import random
from typing import Any

from ..db.models import Database

DISCOVERY_MIN_PAIRS = 8
DISCOVERY_P_THRESHOLD = 0.05
DISCOVERY_MIN_RELATIVE_EFFECT = 0.05  # ≥5% shift vs baseline to matter
PERMUTATION_ITERATIONS = 2000
PERMUTATION_SEED = 7


def _sign_flip_p(deltas: list[float]) -> float:
    """Paired permutation test: under H0 each delta's sign is a coin flip."""
    rng = random.Random(PERMUTATION_SEED)
    observed = abs(sum(deltas) / len(deltas))
    hits = 0
    for _ in range(PERMUTATION_ITERATIONS):
        flipped_mean = sum(d if rng.random() < 0.5 else -d for d in deltas) / len(deltas)
        if abs(flipped_mean) >= observed:
            hits += 1
    return (hits + 1) / (PERMUTATION_ITERATIONS + 1)


def _label_shuffle_p(group_a: list[float], group_b: list[float]) -> float:
    """Two-sample permutation test on the difference of means."""
    rng = random.Random(PERMUTATION_SEED)
    pooled = group_a + group_b
    size_a = len(group_a)
    observed = abs(sum(group_a) / size_a - sum(group_b) / len(group_b))
    hits = 0
    for _ in range(PERMUTATION_ITERATIONS):
        shuffled = pooled[:]
        rng.shuffle(shuffled)
        mean_a = sum(shuffled[:size_a]) / size_a
        mean_b = sum(shuffled[size_a:]) / (len(pooled) - size_a)
        if abs(mean_a - mean_b) >= observed:
            hits += 1
    return (hits + 1) / (PERMUTATION_ITERATIONS + 1)


def gated_paired_effect(deltas: list[float], baseline_mean: float) -> dict[str, Any] | None:
    """Mean paired delta with permutation gate. None unless n, effect size,
    and significance all pass."""
    if len(deltas) < DISCOVERY_MIN_PAIRS or baseline_mean == 0:
        return None
    mean_delta = sum(deltas) / len(deltas)
    relative = mean_delta / abs(baseline_mean)
    if abs(relative) < DISCOVERY_MIN_RELATIVE_EFFECT:
        return None
    p_value = _sign_flip_p(deltas)
    if p_value >= DISCOVERY_P_THRESHOLD:
        return None
    return {
        "n": len(deltas),
        "mean_delta": round(mean_delta, 2),
        "relative_effect": round(relative, 3),
        "p": round(p_value, 4),
    }


DISCOVERY_WINDOW_DAYS = 180
TRACKED_ACTIVITY_TYPES = ("basketball", "skiing", "hiking", "strength", "lap_swimming", "tennis_v2")


def _metrics_by_date(db: Database, days: int) -> dict[str, dict[str, Any]]:
    return {row["date"]: row for row in db.get_recent_metrics(days=days)}


def _next_day(day: str) -> str:
    from datetime import date as date_type, timedelta
    return str(date_type.fromisoformat(day) + timedelta(days=1))


def _activity_next_day_metric(
    db: Database, activity_type: str, metric: str, days: int
) -> dict[str, Any] | None:
    """Paired deltas: metric the morning after each session vs the morning of."""
    metrics = _metrics_by_date(db, days + 1)
    deltas: list[float] = []
    baselines: list[float] = []
    for activity in db.get_recent_activities(days=days, activity_type=activity_type):
        day = str(activity["date"])
        day_value = (metrics.get(day) or {}).get(metric)
        next_value = (metrics.get(_next_day(day)) or {}).get(metric)
        if day_value is None or next_value is None or day_value == 0:
            continue
        deltas.append(next_value - day_value)
        baselines.append(day_value)
    if not baselines:
        return None
    return gated_paired_effect(deltas, baseline_mean=sum(baselines) / len(baselines))


def _period_metric_shift(db: Database, metric: str, days: int) -> dict[str, Any] | None:
    """Two-sample: metric on active-period days vs all other days."""
    period_values: list[float] = []
    other_values: list[float] = []
    for row in db.get_recent_metrics(days=days):
        value = row.get(metric)
        if value is None:
            continue
        phase = str(row.get("menstrual_phase") or "").strip().lower()
        if phase in {"1", "period"}:
            period_values.append(value)
        else:
            other_values.append(value)
    return gated_two_sample_effect(period_values, other_values)


def _consecutive_day_readiness_cost(db: Database, days: int) -> dict[str, Any] | None:
    """Two-sample: readiness on days following a training day vs following rest."""
    from datetime import date as date_type, timedelta
    metrics = _metrics_by_date(db, days + 1)
    activity_dates = {str(a["date"]) for a in db.get_recent_activities(days=days)}
    after_training: list[float] = []
    after_rest: list[float] = []
    for day, row in metrics.items():
        readiness = row.get("training_readiness_score")
        if readiness is None:
            continue
        previous = str(date_type.fromisoformat(day) - timedelta(days=1))
        (after_training if previous in activity_dates else after_rest).append(readiness)
    return gated_two_sample_effect(after_training, after_rest)


def discover_patterns(db: Database, days: int = DISCOVERY_WINDOW_DAYS) -> list[dict[str, Any]]:
    """All gated discovery findings, ready for the insights store."""
    findings: list[dict[str, Any]] = []

    for activity_type in TRACKED_ACTIVITY_TYPES:
        effect = _activity_next_day_metric(db, activity_type, "hrv_last_night", days)
        if effect is not None:
            direction = "drops" if effect["mean_delta"] < 0 else "rises"
            findings.append({
                "key": f"discovery.{activity_type}_next_day_hrv",
                "statement": (
                    f"Your HRV {direction} {abs(effect['relative_effect']) * 100:.0f}% on average"
                    f" the morning after {activity_type} (n={effect['n']} sessions,"
                    f" mean {effect['mean_delta']:+.1f} ms, p={effect['p']})."
                ),
                "evidence": effect,
            })

    period_shift = _period_metric_shift(db, "resting_hr", days)
    if period_shift is not None:
        findings.append({
            "key": "discovery.period_resting_hr_shift",
            "statement": (
                f"Your resting HR runs {abs(period_shift['delta']):.1f} bpm"
                f" {'higher' if period_shift['delta'] > 0 else 'lower'} on active-period days"
                f" (n={period_shift['n_condition']} period days vs"
                f" {period_shift['n_comparison']} other days, p={period_shift['p']})."
            ),
            "evidence": period_shift,
        })

    consecutive = _consecutive_day_readiness_cost(db, days)
    if consecutive is not None:
        findings.append({
            "key": "discovery.consecutive_day_readiness_cost",
            "statement": (
                f"Mornings after a training day your readiness averages"
                f" {abs(consecutive['delta']):.0f} points"
                f" {'lower' if consecutive['delta'] < 0 else 'higher'} than after rest"
                f" (n={consecutive['n_condition']} vs {consecutive['n_comparison']} days,"
                f" p={consecutive['p']})."
            ),
            "evidence": consecutive,
        })

    late_cost = _late_night_cost(db, "sleep_deep_min", SLEEP_WINDOW_DAYS)
    if late_cost is not None and late_cost["delta"] < 0:
        findings.append({
            "key": "sleep.late_night_deep_cost",
            "statement": (
                f"On nights you fall asleep ≥1h later than your usual time, deep sleep"
                f" averages {abs(late_cost['delta']):.0f} min less"
                f" (n={late_cost['n_condition']} late vs {late_cost['n_comparison']} normal"
                f" nights, p={late_cost['p']})."
            ),
            "evidence": late_cost,
        })

    inconsistency = _bedtime_consistency(db)
    if inconsistency is not None:
        findings.append({
            "key": "sleep.bedtime_inconsistency",
            "statement": (
                f"Your bedtime varies ±{inconsistency['std_min']:.0f} min"
                f" (28d, n={inconsistency['n']} nights). Consistency is the single biggest"
                " lever on sleep quality — ahead of duration."
            ),
            "evidence": inconsistency,
        })

    return findings


SLEEP_WINDOW_DAYS = 60
LATE_NIGHT_THRESHOLD_MIN = 60


def _sleep_start_minutes(value: str) -> int:
    """'HH:MM' -> minutes since 18:00 (mod 24h), so 23:30 < 02:56 sorts sanely."""
    hours, minutes = value.split(":")
    return (int(hours) * 60 + int(minutes) - 18 * 60) % 1440


def _late_night_cost(db: Database, outcome_metric: str, days: int) -> dict[str, Any] | None:
    """Two-sample: nights ≥60min later than the personal median vs the rest."""
    nights = [
        (row, _sleep_start_minutes(row["sleep_start"]))
        for row in db.get_recent_metrics(days=days)
        if row.get("sleep_start") and row.get(outcome_metric) is not None
    ]
    if len(nights) < 2 * DISCOVERY_MIN_PAIRS:
        return None
    starts = sorted(minutes for _, minutes in nights)
    median_start = starts[len(starts) // 2]
    late = [row[outcome_metric] for row, minutes in nights
            if minutes - median_start >= LATE_NIGHT_THRESHOLD_MIN]
    normal = [row[outcome_metric] for row, minutes in nights
              if minutes - median_start < LATE_NIGHT_THRESHOLD_MIN]
    return gated_two_sample_effect(late, normal)


BEDTIME_STD_THRESHOLD_MIN = 75
BEDTIME_MIN_NIGHTS = 14
WINDOW_BUCKET_MIN = 30
WINDOW_MIN_BUCKET_N = 5


def _bedtime_consistency(db: Database, days: int = 28) -> dict[str, Any] | None:
    starts = [
        _sleep_start_minutes(row["sleep_start"])
        for row in db.get_recent_metrics(days=days)
        if row.get("sleep_start")
    ]
    if len(starts) < BEDTIME_MIN_NIGHTS:
        return None
    mean = sum(starts) / len(starts)
    std = (sum((s - mean) ** 2 for s in starts) / len(starts)) ** 0.5
    if std <= BEDTIME_STD_THRESHOLD_MIN:
        return None
    return {"std_min": round(std, 1), "n": len(starts), "window_days": days}


def sleep_rhythm_block(db: Database, days: int = SLEEP_WINDOW_DAYS) -> str:
    """Optimal sleep window by half-hour bucket — monthly narrative section."""
    buckets: dict[int, list[float]] = {}
    for row in db.get_recent_metrics(days=days):
        if not row.get("sleep_start") or row.get("sleep_score") is None:
            continue
        bucket = _sleep_start_minutes(row["sleep_start"]) // WINDOW_BUCKET_MIN
        buckets.setdefault(bucket, []).append(row["sleep_score"])
    qualified = {b: scores for b, scores in buckets.items() if len(scores) >= WINDOW_MIN_BUCKET_N}
    lines = ["## Sleep Rhythm (computed)"]
    if not qualified:
        lines.append("Not enough nights per bedtime bucket yet.")
        return "\n".join(lines)
    best = max(qualified, key=lambda b: sum(qualified[b]) / len(qualified[b]))
    start_min = (best * WINDOW_BUCKET_MIN + 18 * 60) % 1440
    end_min = (start_min + WINDOW_BUCKET_MIN) % 1440
    lines.append(
        f"Best-scoring bedtime window: {start_min // 60:02d}:{start_min % 60:02d}"
        f"-{end_min // 60:02d}:{end_min % 60:02d}"
        f" (avg sleep score {sum(qualified[best]) / len(qualified[best]):.0f},"
        f" n={len(qualified[best])} nights)"
    )
    for bucket in sorted(qualified):
        bucket_start = (bucket * WINDOW_BUCKET_MIN + 18 * 60) % 1440
        scores = qualified[bucket]
        lines.append(
            f"- {bucket_start // 60:02d}:{bucket_start % 60:02d}: avg score"
            f" {sum(scores) / len(scores):.0f} (n={len(scores)})"
        )
    return "\n".join(lines)


def store_discovery_findings(db: Database) -> int:
    """Insert new findings; refresh evidence on existing keys. Returns count
    of NEW rows only."""
    inserted = 0
    for finding in discover_patterns(db):
        if db.insert_insight(
            key=finding["key"],
            category="discovery",
            statement=finding["statement"],
            evidence=finding["evidence"],
        ):
            inserted += 1
        else:
            db.refresh_insight_evidence(
                key=finding["key"],
                statement=finding["statement"],
                evidence=finding["evidence"],
            )
    return inserted


def gated_two_sample_effect(
    group_a: list[float], group_b: list[float]
) -> dict[str, Any] | None:
    """Difference of means with permutation gate. group_a is the condition,
    group_b the comparison."""
    if len(group_a) < DISCOVERY_MIN_PAIRS or len(group_b) < DISCOVERY_MIN_PAIRS:
        return None
    mean_a = sum(group_a) / len(group_a)
    mean_b = sum(group_b) / len(group_b)
    if mean_b == 0:
        return None
    relative = (mean_a - mean_b) / abs(mean_b)
    if abs(relative) < DISCOVERY_MIN_RELATIVE_EFFECT:
        return None
    p_value = _label_shuffle_p(group_a, group_b)
    if p_value >= DISCOVERY_P_THRESHOLD:
        return None
    return {
        "n_condition": len(group_a),
        "n_comparison": len(group_b),
        "delta": round(mean_a - mean_b, 2),
        "relative_effect": round(relative, 3),
        "p": round(p_value, 4),
    }
