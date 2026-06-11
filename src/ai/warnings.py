"""Multi-signal illness/overreach early warning.

Distinct from per-metric anomaly detection: this is a composite 'your body is
fighting something' judgment — ≥2 of 4 signals deviating adversely from the
personal 28-day baseline."""
from __future__ import annotations

from typing import Any

from ..db.models import Database

WARNING_BASELINE_DAYS = 28
WARNING_Z_THRESHOLD = 1.5
WARNING_MIN_SIGNALS = 2
WARNING_MIN_BASELINE_SAMPLES = 14
# metric -> adverse direction (+1: elevated is bad, -1: depressed is bad)
WARNING_SIGNALS = {
    "respiration_avg": 1,
    "resting_hr": 1,
    "hrv_last_night": -1,
    "sleep_score": -1,
}
# floors prevent zero/near-zero std from manufacturing infinite z-scores
MIN_STD = {"respiration_avg": 0.5, "resting_hr": 1.0, "hrv_last_night": 2.0, "sleep_score": 3.0}


def health_warning(db: Database) -> dict[str, Any] | None:
    """Today's composite warning, or None."""
    rows = db.get_recent_metrics(days=WARNING_BASELINE_DAYS + 1)
    if len(rows) < WARNING_MIN_BASELINE_SAMPLES + 1:
        return None
    rows_sorted = sorted(rows, key=lambda row: row["date"])
    today = rows_sorted[-1]
    history = rows_sorted[:-1]

    fired: list[str] = []
    details: dict[str, dict[str, float]] = {}
    for metric, adverse_direction in WARNING_SIGNALS.items():
        baseline_values = [r[metric] for r in history if r.get(metric) is not None]
        today_value = today.get(metric)
        if today_value is None or len(baseline_values) < WARNING_MIN_BASELINE_SAMPLES:
            continue
        mean = sum(baseline_values) / len(baseline_values)
        variance = sum((v - mean) ** 2 for v in baseline_values) / len(baseline_values)
        std = max(variance ** 0.5, MIN_STD[metric])
        z_score = (today_value - mean) / std
        details[metric] = {"value": today_value, "baseline": round(mean, 1), "z": round(z_score, 2)}
        if z_score * adverse_direction >= WARNING_Z_THRESHOLD:
            fired.append(metric)

    if len(fired) < WARNING_MIN_SIGNALS:
        return None
    return {"date": today["date"], "fired_signals": fired, "details": details}
