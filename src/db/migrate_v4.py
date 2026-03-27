#!/usr/bin/env python3
"""
Neve DB Migration v4 — Deep Garmin Extraction
Adds ~37 columns to daily_metrics, ~18 to activities, 2 new tables.
Backfills all new columns from existing raw_json.

Run: cd ~/projects/garmin-ai-coach && python3 -m src.db.migrate_v4
Or:  python3 /path/to/migrate_v4.py ~/projects/garmin-ai-coach/data/garmin.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


# ── Schema additions ──────────────────────────────────────────────

DAILY_METRICS_NEW_COLUMNS = [
    # Sleep stages
    ("sleep_deep_min", "INTEGER"),
    ("sleep_light_min", "INTEGER"),
    ("sleep_rem_min", "INTEGER"),
    ("sleep_awake_min", "INTEGER"),
    # Sleep score breakdown
    ("sleep_score_deep", "INTEGER"),
    ("sleep_score_rem", "INTEGER"),
    ("sleep_score_light", "INTEGER"),
    ("sleep_score_restlessness", "TEXT"),
    ("sleep_score_duration", "TEXT"),
    ("sleep_score_awake_count", "TEXT"),
    # Body Battery dynamics
    ("bb_at_wake", "INTEGER"),
    ("bb_highest", "INTEGER"),
    ("bb_lowest", "INTEGER"),
    ("bb_drained", "INTEGER"),
    ("bb_sleep_charge", "INTEGER"),
    ("bb_most_recent", "INTEGER"),
    ("bb_feedback", "TEXT"),
    # Stress breakdown
    ("stress_rest_pct", "REAL"),
    ("stress_low_pct", "REAL"),
    ("stress_medium_pct", "REAL"),
    ("stress_high_pct", "REAL"),
    # Respiration
    ("respiration_avg", "REAL"),
    ("respiration_high", "REAL"),
    ("respiration_low", "REAL"),
    # HRV detail
    ("hrv_range_low", "INTEGER"),
    ("hrv_range_high", "INTEGER"),
    ("hrv_status", "TEXT"),
    ("hrv_reading_count", "INTEGER"),
    # Movement
    ("steps", "INTEGER"),
    ("active_minutes", "INTEGER"),
    ("sedentary_hours", "REAL"),
    ("intensity_minutes_vigorous", "INTEGER"),
    ("intensity_minutes_moderate", "INTEGER"),
    ("floors_ascended", "REAL"),
    # Menstrual cycle
    ("menstrual_phase", "TEXT"),
    ("menstrual_day_of_cycle", "INTEGER"),
    # Misc
    ("endurance_score", "REAL"),
    ("restless_moments", "INTEGER"),
]

ACTIVITIES_NEW_COLUMNS = [
    ("activity_name", "TEXT"),
    ("weather_temp_c", "REAL"),
    ("weather_condition", "TEXT"),
    ("weather_wind_kmh", "REAL"),
    ("weather_humidity", "INTEGER"),
    ("elevation_gain", "REAL"),
    ("elevation_loss", "REAL"),
    ("max_elevation", "REAL"),
    ("hr_zone1_sec", "INTEGER"),
    ("hr_zone2_sec", "INTEGER"),
    ("hr_zone3_sec", "INTEGER"),
    ("hr_zone4_sec", "INTEGER"),
    ("hr_zone5_sec", "INTEGER"),
    ("distance_m", "REAL"),
    ("avg_speed", "REAL"),
    ("max_speed", "REAL"),
    ("training_effect_label", "TEXT"),
    ("start_time", "TEXT"),
]

NEW_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS concerns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_date TEXT NOT NULL,
    concern TEXT NOT NULL,
    impact TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    resolved_date TEXT,
    sport_affected TEXT,
    source TEXT NOT NULL DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS basketball_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id TEXT REFERENCES activities(id),
    date TEXT NOT NULL,
    estimated_load REAL NOT NULL DEFAULT 90,
    duration_min REAL,
    intensity TEXT DEFAULT 'high',
    notes TEXT,
    UNIQUE(date, activity_id)
);
"""


# ── Backfill extractors ──────────────────────────────────────────

def extract_daily_metrics_from_raw(raw_json: str) -> dict:
    """Extract all new columns from daily_metrics.raw_json."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return {}

    # Navigate the raw structure: could be {stats, hrv, sleep, stress, body_battery}
    # or flat (the stats blob itself)
    raw = data.get("raw", data) if isinstance(data, dict) else data
    stats = raw.get("stats", raw) if isinstance(raw, dict) else {}
    sleep_raw = raw.get("sleep", {}) if isinstance(raw, dict) else {}
    hrv_raw = raw.get("hrv", {}) if isinstance(raw, dict) else {}
    stress_raw = raw.get("stress", {}) if isinstance(raw, dict) else {}

    result = {}

    # ── Sleep stages ──
    daily_sleep = sleep_raw.get("dailySleepDTO", {}) if isinstance(sleep_raw, dict) else {}
    deep_sec = daily_sleep.get("deepSleepSeconds")
    if deep_sec is not None:
        result["sleep_deep_min"] = round(deep_sec / 60)
    light_sec = daily_sleep.get("lightSleepSeconds")
    if light_sec is not None:
        result["sleep_light_min"] = round(light_sec / 60)
    rem_sec = daily_sleep.get("remSleepSeconds")
    if rem_sec is not None:
        result["sleep_rem_min"] = round(rem_sec / 60)
    awake_sec = daily_sleep.get("awakeSleepSeconds")
    if awake_sec is not None:
        result["sleep_awake_min"] = round(awake_sec / 60)

    # ── Sleep score breakdown ──
    scores = daily_sleep.get("sleepScores", {})
    if isinstance(scores, dict):
        for field, key in [
            ("sleep_score_deep", "deepPercentage"),
            ("sleep_score_rem", "remPercentage"),
            ("sleep_score_light", "lightPercentage"),
        ]:
            entry = scores.get(key, {})
            if isinstance(entry, dict) and entry.get("value") is not None:
                result[field] = entry["value"]

        for field, key in [
            ("sleep_score_restlessness", "restlessness"),
            ("sleep_score_duration", "totalDuration"),
            ("sleep_score_awake_count", "awakeCount"),
        ]:
            entry = scores.get(key, {})
            if isinstance(entry, dict) and entry.get("qualifierKey") is not None:
                result[field] = entry["qualifierKey"]

    # ── Body Battery dynamics ──
    if isinstance(stats, dict):
        bb_map = {
            "bb_at_wake": "bodyBatteryAtWakeTime",
            "bb_highest": "bodyBatteryHighestValue",
            "bb_lowest": "bodyBatteryLowestValue",
            "bb_drained": "bodyBatteryDrainedValue",
            "bb_most_recent": "bodyBatteryMostRecentValue",
        }
        for col, key in bb_map.items():
            val = stats.get(key)
            if val is not None:
                result[col] = int(val)

        bb_during_sleep = stats.get("bodyBatteryDuringSleep")
        if bb_during_sleep is not None:
            result["bb_sleep_charge"] = int(bb_during_sleep)

        feedback_event = stats.get("bodyBatteryDynamicFeedbackEvent", {})
        if isinstance(feedback_event, dict):
            result["bb_feedback"] = feedback_event.get("feedbackLongType")

    # ── Stress breakdown ──
    if isinstance(stats, dict):
        stress_map = {
            "stress_rest_pct": "restStressPercentage",
            "stress_low_pct": "lowStressPercentage",
            "stress_medium_pct": "mediumStressPercentage",
            "stress_high_pct": "highStressPercentage",
        }
        for col, key in stress_map.items():
            val = stats.get(key)
            if val is not None:
                result[col] = float(val)

    # ── Respiration ──
    if isinstance(stats, dict):
        resp_map = {
            "respiration_avg": "avgWakingRespirationValue",
            "respiration_high": "highestRespirationValue",
            "respiration_low": "lowestRespirationValue",
        }
        for col, key in resp_map.items():
            val = stats.get(key)
            if val is not None:
                result[col] = float(val)

    # ── HRV detail ──
    if isinstance(hrv_raw, dict):
        readings = hrv_raw.get("hrvReadings", [])
        if readings:
            vals = [r["hrvValue"] for r in readings if r.get("hrvValue") is not None]
            if vals:
                result["hrv_range_low"] = min(vals)
                result["hrv_range_high"] = max(vals)
                result["hrv_reading_count"] = len(vals)

    if isinstance(sleep_raw, dict):
        hrv_status = sleep_raw.get("hrvStatus")
        if hrv_status is not None:
            result["hrv_status"] = hrv_status

    # ── Movement ──
    if isinstance(stats, dict):
        steps = stats.get("totalSteps")
        if steps is not None:
            result["steps"] = int(steps)

        active_sec = stats.get("activeSeconds")
        if active_sec is not None:
            result["active_minutes"] = round(active_sec / 60)

        sedentary_sec = stats.get("sedentarySeconds")
        if sedentary_sec is not None:
            result["sedentary_hours"] = round(sedentary_sec / 3600, 1)

        vig_min = stats.get("vigorousIntensityMinutes")
        if vig_min is not None:
            result["intensity_minutes_vigorous"] = int(vig_min)

        mod_min = stats.get("moderateIntensityMinutes")
        if mod_min is not None:
            result["intensity_minutes_moderate"] = int(mod_min)

        floors = stats.get("floorsAscended")
        if floors is not None:
            result["floors_ascended"] = float(floors)

    # ── Restless moments ──
    if isinstance(sleep_raw, dict):
        restless = sleep_raw.get("restlessMomentsCount")
        if restless is not None:
            result["restless_moments"] = int(restless)

    return result


def extract_activity_from_raw(raw_json: str) -> dict:
    """Extract all new columns from activities.raw_json."""
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return {}

    raw = data.get("raw", data) if isinstance(data, dict) else data
    if not isinstance(raw, dict):
        return {}

    result = {}

    # Activity name
    name = raw.get("activityName")
    if name is not None:
        result["activity_name"] = str(name)

    # Elevation
    for col, key in [
        ("elevation_gain", "elevationGain"),
        ("elevation_loss", "elevationLoss"),
        ("max_elevation", "maxElevation"),
    ]:
        val = raw.get(key)
        if val is not None:
            result[col] = float(val)

    # Distance
    distance = raw.get("distance")
    if distance is not None:
        result["distance_m"] = float(distance)

    # Speed
    avg_speed = raw.get("averageSpeed")
    if avg_speed is not None:
        result["avg_speed"] = float(avg_speed)
    max_speed = raw.get("maxSpeed")
    if max_speed is not None:
        result["max_speed"] = float(max_speed)

    # Training effect label
    te_label = raw.get("trainingEffectLabel")
    if te_label is not None:
        result["training_effect_label"] = str(te_label)

    # Start time
    start_time = raw.get("startTimeLocal")
    if start_time is not None:
        result["start_time"] = str(start_time)

    # HR zones from splitSummaries (if available)
    splits = raw.get("summarizedExerciseSets") or []
    # HR zones are sometimes in a different location — check activityDetailMetrics
    # For now, these will be populated by a separate API call during sync

    return result


# ── Migration runner ─────────────────────────────────────────────

def migrate(db_path: str | Path) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Check current version
    try:
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        current_version = row["version"] if row else 0
    except sqlite3.OperationalError:
        current_version = 0

    if current_version >= 4:
        print(f"Already at schema version {current_version}, nothing to do.")
        conn.close()
        return

    print(f"Migrating from v{current_version} → v4...")

    # ── Step 1: Add new columns ──
    added = 0
    for col_name, col_type in DAILY_METRICS_NEW_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE daily_metrics ADD COLUMN {col_name} {col_type}")
            added += 1
        except sqlite3.OperationalError:
            pass  # already exists

    for col_name, col_type in ACTIVITIES_NEW_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {col_name} {col_type}")
            added += 1
        except sqlite3.OperationalError:
            pass

    print(f"  Added {added} new columns")

    # ── Step 2: Create new tables ──
    conn.executescript(NEW_TABLES_SQL)
    print("  Created tables: concerns, basketball_corrections")

    # ── Step 3: Backfill daily_metrics from raw_json ──
    rows = conn.execute("SELECT date, raw_json FROM daily_metrics WHERE raw_json IS NOT NULL").fetchall()
    backfilled_metrics = 0
    for row in rows:
        extracted = extract_daily_metrics_from_raw(row["raw_json"])
        if extracted:
            set_clauses = ", ".join(f"{k} = ?" for k in extracted)
            values = list(extracted.values()) + [row["date"]]
            conn.execute(
                f"UPDATE daily_metrics SET {set_clauses} WHERE date = ?",
                values,
            )
            backfilled_metrics += 1

    print(f"  Backfilled {backfilled_metrics}/{len(rows)} daily_metrics rows")

    # ── Step 4: Backfill activities from raw_json ──
    rows = conn.execute("SELECT id, raw_json FROM activities WHERE raw_json IS NOT NULL").fetchall()
    backfilled_activities = 0
    for row in rows:
        extracted = extract_activity_from_raw(row["raw_json"])
        if extracted:
            set_clauses = ", ".join(f"{k} = ?" for k in extracted)
            values = list(extracted.values()) + [row["id"]]
            conn.execute(
                f"UPDATE activities SET {set_clauses} WHERE id = ?",
                values,
            )
            backfilled_activities += 1

    print(f"  Backfilled {backfilled_activities}/{len(rows)} activity rows")

    # ── Step 5: Auto-create basketball corrections for known basketball activities ──
    bball_rows = conn.execute(
        "SELECT id, date, duration_min, training_load FROM activities WHERE type = 'basketball'"
    ).fetchall()
    for brow in bball_rows:
        garmin_load = brow["training_load"] or 0
        conn.execute(
            """INSERT OR IGNORE INTO basketball_corrections
               (activity_id, date, estimated_load, duration_min, intensity, notes)
               VALUES (?, ?, ?, ?, 'high',
                       'Auto-created: Garmin load ' || ? || ' is unreliable (watch not worn during game)')""",
            (brow["id"], brow["date"], 90.0, brow["duration_min"], str(round(garmin_load, 1))),
        )
    if bball_rows:
        print(f"  Created {len(bball_rows)} basketball correction entries")

    # ── Step 6: Update schema version ──
    conn.execute("UPDATE schema_version SET version = 4 WHERE version = ?", (current_version,))
    if conn.execute("SELECT changes()").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version (version) VALUES (4)")

    conn.commit()
    conn.close()

    print("Migration v4 complete.")


def verify(db_path: str | Path) -> None:
    """Print a summary of the migrated DB."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    version = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
    print(f"\nSchema version: {version['version'] if version else '?'}")

    # Count columns per table
    for table in ["daily_metrics", "activities"]:
        cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
        print(f"{table}: {len(cols)} columns")

    # Sample latest daily_metrics with new columns
    row = conn.execute("""
        SELECT date, sleep_deep_min, sleep_light_min, sleep_rem_min,
               bb_at_wake, bb_highest, bb_lowest, bb_sleep_charge, bb_feedback,
               stress_rest_pct, stress_high_pct,
               respiration_avg,
               hrv_range_low, hrv_range_high, hrv_status, hrv_reading_count,
               steps, sedentary_hours, restless_moments
        FROM daily_metrics ORDER BY date DESC LIMIT 1
    """).fetchone()
    if row:
        print(f"\nLatest daily_metrics ({row['date']}):")
        for key in row.keys():
            if key != "date":
                print(f"  {key}: {row[key]}")

    # Sample latest activity with new columns
    row = conn.execute("""
        SELECT id, date, type, activity_name,
               elevation_gain, elevation_loss, max_elevation,
               distance_m, avg_speed, max_speed,
               training_effect_label, start_time
        FROM activities ORDER BY date DESC LIMIT 1
    """).fetchone()
    if row:
        print(f"\nLatest activity ({row['date']} {row['type']}):")
        for key in row.keys():
            if key not in ("id", "date", "type"):
                print(f"  {key}: {row[key]}")

    # New tables
    for table in ["concerns", "basketball_corrections"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"\n{table}: {count} rows")

    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        db = sys.argv[1]
    else:
        db = Path.home() / "projects" / "garmin-ai-coach" / "data" / "garmin.db"

    migrate(db)
    verify(db)
