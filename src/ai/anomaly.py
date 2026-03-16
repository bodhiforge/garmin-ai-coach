"""Open-ended anomaly detection — find patterns the developer didn't anticipate."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from ..db.models import Database

logger = logging.getLogger(__name__)


def detect_anomalies(db: Database) -> list[dict[str, Any]]:
    """Scan all metrics and activity data for statistical anomalies (>2 sigma).
    Returns list of {metric, value, baseline, sigma, deviation, date, description}."""

    anomalies = []
    anomalies.extend(_metric_anomalies(db))
    anomalies.extend(_activity_anomalies(db))
    anomalies.extend(_cross_metric_anomalies(db))

    # Sort by deviation magnitude
    anomalies.sort(key=lambda a: abs(a.get("deviation", 0)), reverse=True)
    return anomalies


def _stats(values: list[float]) -> tuple[float, float]:
    """Return (mean, std_dev) for a list of values."""
    if len(values) < 3:
        return 0, 0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, variance ** 0.5


def _metric_anomalies(db: Database) -> list[dict]:
    """Check each daily metric for 2-sigma deviations."""
    metrics = db.get_recent_metrics(days=90)
    if len(metrics) < 14:
        return []

    results = []
    latest = metrics[0]
    latest_date = latest["date"]

    checks = [
        ("hrv_last_night", "HRV", "ms"),
        ("sleep_duration_min", "Sleep duration", "min"),
        ("resting_hr", "Resting HR", "bpm"),
        ("body_battery_am", "Body Battery", ""),
        ("stress_avg", "Avg stress", ""),
        ("training_readiness_score", "Training Readiness", ""),
    ]

    for field, name, unit in checks:
        values = [m[field] for m in metrics if m.get(field) is not None]
        if len(values) < 7:
            continue

        mean, std = _stats(values)
        if std == 0:
            continue

        latest_val = latest.get(field)
        if latest_val is None:
            continue

        deviation = (latest_val - mean) / std

        if abs(deviation) >= 2:
            direction = "unusually high" if deviation > 0 else "unusually low"
            results.append({
                "metric": name,
                "value": latest_val,
                "baseline": mean,
                "sigma": std,
                "deviation": deviation,
                "date": latest_date,
                "description": f"{name} is {direction}: {latest_val}{unit} vs baseline {mean:.0f}{unit} ({abs(deviation):.1f} sigma)",
            })

    return results


def _activity_anomalies(db: Database) -> list[dict]:
    """Check activity metrics for unusual sessions."""
    results = []

    # Ski speed anomalies
    ski = db.get_recent_activities(days=365, activity_type="skiing")
    if len(ski) >= 5:
        session_speeds = []
        for a in ski:
            runs = db.get_ski_runs(a["id"])
            if runs:
                max_s = max((r.get("max_speed_kmh", 0) or 0 for r in runs), default=0)
                if max_s > 0:
                    session_speeds.append((a["date"], max_s))

        if len(session_speeds) >= 5:
            speeds = [s for _, s in session_speeds]
            mean, std = _stats(speeds)
            if std > 0:
                latest_date, latest_speed = session_speeds[0]
                dev = (latest_speed - mean) / std
                if abs(dev) >= 2:
                    direction = "exceptionally fast" if dev > 0 else "unusually slow"
                    results.append({
                        "metric": "Ski max speed",
                        "value": latest_speed,
                        "baseline": mean,
                        "sigma": std,
                        "deviation": dev,
                        "date": latest_date,
                        "description": f"Last ski session was {direction}: {latest_speed:.1f} km/h vs avg {mean:.1f} km/h ({abs(dev):.1f} sigma)",
                    })

    # Gym session volume anomalies
    gym = db.get_recent_activities(days=365, activity_type="strength")
    if len(gym) >= 5:
        session_volumes = []
        for a in gym:
            sets = db.get_gym_sets(a["id"])
            if sets:
                vol = sum((s.get("weight_kg", 0) or 0) * (s.get("reps", 0) or 0) for s in sets)
                if vol > 0:
                    session_volumes.append((a["date"], vol))

        if len(session_volumes) >= 5:
            vols = [v for _, v in session_volumes]
            mean, std = _stats(vols)
            if std > 0:
                latest_date, latest_vol = session_volumes[0]
                dev = (latest_vol - mean) / std
                if abs(dev) >= 2:
                    direction = "exceptionally high" if dev > 0 else "unusually low"
                    results.append({
                        "metric": "Gym session volume",
                        "value": latest_vol,
                        "baseline": mean,
                        "sigma": std,
                        "deviation": dev,
                        "date": latest_date,
                        "description": f"Last gym volume was {direction}: {latest_vol:.0f} kg vs avg {mean:.0f} kg ({abs(dev):.1f} sigma)",
                    })

    return results


def _cross_metric_anomalies(db: Database) -> list[dict]:
    """Detect unusual combinations across metrics."""
    metrics = db.get_recent_metrics(days=30)
    if len(metrics) < 7:
        return []

    results = []
    latest = metrics[0]

    # HRV high but Body Battery low (or vice versa) — unusual divergence
    hrv = latest.get("hrv_last_night")
    bb = latest.get("body_battery_am")
    if hrv and bb:
        hrv_values = [m["hrv_last_night"] for m in metrics if m.get("hrv_last_night")]
        bb_values = [m["body_battery_am"] for m in metrics if m.get("body_battery_am")]
        if len(hrv_values) >= 7 and len(bb_values) >= 7:
            hrv_mean, hrv_std = _stats(hrv_values)
            bb_mean, bb_std = _stats(bb_values)
            if hrv_std > 0 and bb_std > 0:
                hrv_z = (hrv - hrv_mean) / hrv_std
                bb_z = (bb - bb_mean) / bb_std
                # Divergence: one high and other low
                if hrv_z > 1.5 and bb_z < -1.5:
                    results.append({
                        "metric": "HRV-BB divergence",
                        "value": None,
                        "baseline": None,
                        "sigma": None,
                        "deviation": abs(hrv_z - bb_z),
                        "date": latest["date"],
                        "description": f"HRV is high ({hrv:.0f}ms, +{hrv_z:.1f}σ) but Body Battery is low ({bb}, {bb_z:.1f}σ). Unusual — possible stress or disrupted sleep quality despite good autonomic recovery.",
                    })
                elif hrv_z < -1.5 and bb_z > 1.5:
                    results.append({
                        "metric": "HRV-BB divergence",
                        "value": None,
                        "baseline": None,
                        "sigma": None,
                        "deviation": abs(hrv_z - bb_z),
                        "date": latest["date"],
                        "description": f"HRV is low ({hrv:.0f}ms, {hrv_z:.1f}σ) but Body Battery is high ({bb}, +{bb_z:.1f}σ). May indicate recent intense training with good subjective rest.",
                    })

    return results


def format_anomalies(anomalies: list[dict]) -> str:
    """Format anomalies for display or injection into LLM context."""
    if not anomalies:
        return "No anomalies detected — all metrics within normal range."

    lines = [f"Found {len(anomalies)} anomaly(ies):"]
    for a in anomalies:
        lines.append(f"  [{abs(a['deviation']):.1f}σ] {a['description']}")

    return "\n".join(lines)
