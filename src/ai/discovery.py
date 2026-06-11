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

    return findings


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
