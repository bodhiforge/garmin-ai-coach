from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Generator


SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS daily_metrics (
    date TEXT PRIMARY KEY,
    hrv_weekly_avg REAL,
    hrv_last_night REAL,
    sleep_duration_min INTEGER,
    sleep_score INTEGER,
    body_battery_am INTEGER,
    stress_avg INTEGER,
    resting_hr INTEGER,
    spo2_avg REAL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    type TEXT NOT NULL,
    duration_min REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    calories INTEGER,
    summary_json TEXT,
    fit_file_path TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS gym_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id TEXT NOT NULL REFERENCES activities(id),
    set_number INTEGER NOT NULL,
    exercise TEXT,
    reps INTEGER,
    weight_kg REAL,
    peak_hr INTEGER,
    recovery_hr INTEGER,
    rest_duration_sec INTEGER,
    UNIQUE(activity_id, set_number)
);

CREATE TABLE IF NOT EXISTS ski_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id TEXT NOT NULL REFERENCES activities(id),
    run_number INTEGER NOT NULL,
    max_speed_kmh REAL,
    avg_speed_kmh REAL,
    vertical_drop_m REAL,
    duration_sec INTEGER,
    max_hr INTEGER,
    lift_top_hr INTEGER,
    UNIQUE(activity_id, run_number)
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    role TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(SCHEMA_SQL)
            existing = conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )

    # -- Daily Metrics --

    def upsert_daily_metrics(self, metrics: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO daily_metrics
                   (date, hrv_weekly_avg, hrv_last_night, sleep_duration_min,
                    sleep_score, body_battery_am, stress_avg, resting_hr, spo2_avg, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                    hrv_weekly_avg=excluded.hrv_weekly_avg,
                    hrv_last_night=excluded.hrv_last_night,
                    sleep_duration_min=excluded.sleep_duration_min,
                    sleep_score=excluded.sleep_score,
                    body_battery_am=excluded.body_battery_am,
                    stress_avg=excluded.stress_avg,
                    resting_hr=excluded.resting_hr,
                    spo2_avg=excluded.spo2_avg,
                    raw_json=excluded.raw_json""",
                (
                    metrics["date"],
                    metrics.get("hrv_weekly_avg"),
                    metrics.get("hrv_last_night"),
                    metrics.get("sleep_duration_min"),
                    metrics.get("sleep_score"),
                    metrics.get("body_battery_am"),
                    metrics.get("stress_avg"),
                    metrics.get("resting_hr"),
                    metrics.get("spo2_avg"),
                    json.dumps(metrics.get("raw"), ensure_ascii=False)
                    if metrics.get("raw")
                    else None,
                ),
            )

    def get_daily_metrics(self, target_date: str | date | None = None) -> dict[str, Any] | None:
        if target_date is None:
            # Return most recent day's metrics
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT 1"
                ).fetchone()
                return dict(row) if row else None
        date_str = str(target_date)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM daily_metrics WHERE date = ?", (date_str,)
            ).fetchone()
            return dict(row) if row else None

    def get_recent_metrics(self, days: int = 7) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT ?", (days,)
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Activities --

    def upsert_activity(self, activity: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO activities
                   (id, date, type, duration_min, avg_hr, max_hr, calories,
                    summary_json, fit_file_path, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    summary_json=excluded.summary_json,
                    fit_file_path=excluded.fit_file_path""",
                (
                    str(activity["id"]),
                    activity["date"],
                    activity["type"],
                    activity.get("duration_min"),
                    activity.get("avg_hr"),
                    activity.get("max_hr"),
                    activity.get("calories"),
                    activity.get("summary_json"),
                    activity.get("fit_file_path"),
                    json.dumps(activity.get("raw"), ensure_ascii=False)
                    if activity.get("raw")
                    else None,
                ),
            )

    def activity_exists(self, activity_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM activities WHERE id = ?", (str(activity_id),)
            ).fetchone()
            return row is not None

    def get_recent_activities(
        self, days: int = 7, activity_type: str | None = None
    ) -> list[dict[str, Any]]:
        with self._connection() as conn:
            if activity_type is not None:
                rows = conn.execute(
                    """SELECT * FROM activities
                       WHERE date >= date('now', ? || ' days') AND type = ?
                       ORDER BY date DESC""",
                    (f"-{days}", activity_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM activities
                       WHERE date >= date('now', ? || ' days')
                       ORDER BY date DESC""",
                    (f"-{days}",),
                ).fetchall()
            return [dict(r) for r in rows]

    # -- Gym Sets --

    def insert_gym_sets(self, activity_id: str, sets: list[dict[str, Any]]) -> None:
        with self._connection() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO gym_sets
                   (activity_id, set_number, exercise, reps, weight_kg,
                    peak_hr, recovery_hr, rest_duration_sec)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        str(activity_id),
                        s["set_number"],
                        s.get("exercise"),
                        s.get("reps"),
                        s.get("weight_kg"),
                        s.get("peak_hr"),
                        s.get("recovery_hr"),
                        s.get("rest_duration_sec"),
                    )
                    for s in sets
                ],
            )

    def get_gym_sets(self, activity_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM gym_sets WHERE activity_id = ? ORDER BY set_number",
                (str(activity_id),),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Ski Runs --

    def insert_ski_runs(self, activity_id: str, runs: list[dict[str, Any]]) -> None:
        with self._connection() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO ski_runs
                   (activity_id, run_number, max_speed_kmh, avg_speed_kmh,
                    vertical_drop_m, duration_sec, max_hr, lift_top_hr)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        str(activity_id),
                        r["run_number"],
                        r.get("max_speed_kmh"),
                        r.get("avg_speed_kmh"),
                        r.get("vertical_drop_m"),
                        r.get("duration_sec"),
                        r.get("max_hr"),
                        r.get("lift_top_hr"),
                    )
                    for r in runs
                ],
            )

    def get_ski_runs(self, activity_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM ski_runs WHERE activity_id = ? ORDER BY run_number",
                (str(activity_id),),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Chat History --

    def add_chat_message(self, role: str, message: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO chat_history (timestamp, role, message) VALUES (?, ?, ?)",
                (datetime.now().isoformat(), role, message),
            )

    def get_recent_chat(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    # -- Notifications --

    def add_notification(self, notif_type: str, content: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO notifications (timestamp, type, content) VALUES (?, ?, ?)",
                (datetime.now().isoformat(), notif_type, content),
            )

    def get_last_notification(self, notif_type: str | None = None) -> dict[str, Any] | None:
        with self._connection() as conn:
            if notif_type is not None:
                row = conn.execute(
                    "SELECT * FROM notifications WHERE type = ? ORDER BY id DESC LIMIT 1",
                    (notif_type,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM notifications ORDER BY id DESC LIMIT 1"
                ).fetchone()
            return dict(row) if row else None

    def hours_since_last_notification(self, notif_type: str | None = None) -> float:
        last = self.get_last_notification(notif_type)
        if last is None:
            return 999.0
        from datetime import datetime as dt
        last_time = dt.fromisoformat(last["timestamp"])
        return (dt.now() - last_time).total_seconds() / 3600
