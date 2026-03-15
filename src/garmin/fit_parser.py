from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import fitdecode

logger = logging.getLogger(__name__)


def parse_gym_session(fit_path: str | Path) -> list[dict[str, Any]]:
    """Parse a strength training FIT file into per-set data with HR metrics."""
    sets: list[dict[str, Any]] = []
    hr_samples: list[tuple[float, int]] = []  # (timestamp_s, hr)

    with fitdecode.FitReader(str(fit_path)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue

            if frame.name == "record":
                ts = _get_field(frame, "timestamp")
                hr = _get_field(frame, "heart_rate")
                if ts is not None and hr is not None:
                    hr_samples.append((ts.timestamp(), int(hr)))

            elif frame.name == "set":
                set_type = _get_field(frame, "set_type")
                # set_type 0 = active set, 1 = rest
                if set_type == 0:
                    exercise_name = _get_field(frame, "exercise_name")
                    category = _get_field(frame, "category")
                    reps = _get_field(frame, "repetitions")
                    weight = _get_field(frame, "weight_display")
                    start_time = _get_field(frame, "start_time")
                    duration = _get_field(frame, "duration")

                    exercise = exercise_name or category or "unknown"
                    if isinstance(exercise, int):
                        exercise = f"exercise_{exercise}"

                    set_data: dict[str, Any] = {
                        "set_number": len(sets) + 1,
                        "exercise": str(exercise),
                        "reps": int(reps) if reps is not None else None,
                        "weight_kg": round(float(weight), 1) if weight is not None else None,
                        "peak_hr": None,
                        "recovery_hr": None,
                        "rest_duration_sec": None,
                    }

                    # Find peak HR during this set
                    if start_time is not None and duration is not None:
                        set_start = start_time.timestamp()
                        set_end = set_start + float(duration)
                        set_hrs = [
                            hr for ts, hr in hr_samples
                            if set_start <= ts <= set_end
                        ]
                        if set_hrs:
                            set_data["peak_hr"] = max(set_hrs)

                    sets.append(set_data)

    # Calculate recovery HR and rest duration between sets
    _calculate_recovery(sets, hr_samples)

    return sets


def _calculate_recovery(
    sets: list[dict[str, Any]], hr_samples: list[tuple[float, int]]
) -> None:
    """Fill in recovery_hr and rest_duration_sec between consecutive sets."""
    if len(sets) < 2 or not hr_samples:
        return

    # We approximate rest periods by looking at HR drops between sets
    # For each set after the first, find the minimum HR between the previous
    # set's peak and this set's start
    for i in range(1, len(sets)):
        prev_set = sets[i - 1]
        curr_set = sets[i]

        prev_peak = prev_set.get("peak_hr")
        curr_peak = curr_set.get("peak_hr")
        if prev_peak is None or curr_peak is None:
            continue

        # Find HR samples between sets (approximate using indices)
        # This is simplified - real implementation would use timestamps
        rest_hrs = [
            hr for _, hr in hr_samples
            if hr < prev_peak
        ]
        if rest_hrs:
            prev_set["recovery_hr"] = min(rest_hrs[-10:]) if len(rest_hrs) >= 10 else min(rest_hrs)


def parse_ski_session(fit_path: str | Path) -> list[dict[str, Any]]:
    """Parse a skiing FIT file into per-run data."""
    runs: list[dict[str, Any]] = []
    current_run_hrs: list[int] = []
    hr_samples: list[tuple[float, int]] = []
    lap_data: list[dict[str, Any]] = []

    with fitdecode.FitReader(str(fit_path)) as fit:
        for frame in fit:
            if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                continue

            if frame.name == "record":
                hr = _get_field(frame, "heart_rate")
                ts = _get_field(frame, "timestamp")
                if hr is not None and ts is not None:
                    hr_samples.append((ts.timestamp(), int(hr)))

            elif frame.name == "lap":
                lap = _extract_lap(frame)
                if lap is not None:
                    lap_data.append(lap)

    # In ski mode, each "lap" typically represents a run (descent)
    # Filter for descent laps (positive vertical drop, reasonable speed)
    run_number = 0
    for i, lap in enumerate(lap_data):
        total_descent = lap.get("total_descent", 0) or 0
        avg_speed = lap.get("avg_speed_kmh", 0) or 0

        # A real ski run typically has >20m descent and >5 km/h avg speed
        if total_descent > 20 and avg_speed > 5:
            run_number += 1

            # Find HR at the end of this lap (lift top = start of next rest)
            lift_top_hr = _find_lift_top_hr(lap, hr_samples, lap_data, i)

            runs.append({
                "run_number": run_number,
                "max_speed_kmh": lap.get("max_speed_kmh"),
                "avg_speed_kmh": round(avg_speed, 1),
                "vertical_drop_m": round(total_descent, 1),
                "duration_sec": lap.get("duration_sec"),
                "max_hr": lap.get("max_hr"),
                "lift_top_hr": lift_top_hr,
            })

    return runs


def _extract_lap(frame: fitdecode.FitDataMessage) -> dict[str, Any] | None:
    total_descent = _get_field(frame, "total_descent")
    max_speed = _get_field(frame, "enhanced_max_speed") or _get_field(frame, "max_speed")
    avg_speed = _get_field(frame, "enhanced_avg_speed") or _get_field(frame, "avg_speed")

    start_time = _get_field(frame, "start_time")
    duration = _get_field(frame, "total_elapsed_time")

    return {
        "total_descent": float(total_descent) if total_descent is not None else None,
        "max_speed_kmh": round(float(max_speed) * 3.6, 1) if max_speed is not None else None,
        "avg_speed_kmh": round(float(avg_speed) * 3.6, 1) if avg_speed is not None else None,
        "duration_sec": int(float(duration)) if duration is not None else None,
        "max_hr": _get_field(frame, "max_heart_rate"),
        "start_time": start_time.timestamp() if start_time is not None else None,
        "end_time": (start_time.timestamp() + float(duration))
        if start_time is not None and duration is not None
        else None,
    }


def _find_lift_top_hr(
    lap: dict, hr_samples: list[tuple[float, int]],
    all_laps: list[dict], lap_index: int
) -> int | None:
    """Find HR when reaching the top of the lift (start of next lap)."""
    if lap_index + 1 >= len(all_laps):
        return None

    next_lap = all_laps[lap_index + 1]
    next_start = next_lap.get("start_time")
    if next_start is None:
        return None

    # Find HR closest to the start of the next lap
    closest_hr = None
    closest_diff = float("inf")
    for ts, hr in hr_samples:
        diff = abs(ts - next_start)
        if diff < closest_diff:
            closest_diff = diff
            closest_hr = hr

    return closest_hr


def _get_field(frame: fitdecode.FitDataMessage, name: str) -> Any:
    try:
        field = frame.get_field(name)
        return field.value if field is not None else None
    except KeyError:
        return None
