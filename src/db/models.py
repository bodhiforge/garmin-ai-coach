from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Generator


SCHEMA_VERSION = 6

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
    training_readiness_score INTEGER,
    training_readiness_level TEXT,
    recovery_time_hours INTEGER,
    acute_load REAL,
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
    aerobic_te REAL,
    anaerobic_te REAL,
    training_load REAL,
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
    weight_lb REAL,
    peak_hr INTEGER,
    recovery_hr INTEGER,
    rest_duration_sec INTEGER,
    UNIQUE(activity_id, set_number)
);

CREATE TABLE IF NOT EXISTS manual_gym_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id TEXT NOT NULL REFERENCES activities(id),
    exercise TEXT NOT NULL,
    reps INTEGER,
    weight_lb REAL,
    set_count INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id TEXT NOT NULL REFERENCES activities(id),
    rpe REAL,
    pain_area TEXT,
    pain_level INTEGER,
    menstrual_symptoms TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS conversation_state (
    chat_id TEXT PRIMARY KEY,
    messages_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
            current_version = existing["version"] if existing is not None else 0

            if current_version < 2:
                self._migrate_v2(conn)
            if current_version < 3:
                self._migrate_v3(conn)
            if current_version < 4:
                self._migrate_v4(conn)
            if current_version < 5:
                self._migrate_v5(conn)
            if current_version < 6:
                self._migrate_v6(conn)

            if existing is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (SCHEMA_VERSION,),
                )
            elif current_version < SCHEMA_VERSION:
                conn.execute(
                    "UPDATE schema_version SET version = ? WHERE version = ?",
                    (SCHEMA_VERSION, current_version),
                )

    @staticmethod
    def _migrate_v2(conn: sqlite3.Connection) -> None:
        """Add training readiness to daily_metrics, training effect to activities."""
        new_columns = [
            ("daily_metrics", "training_readiness_score", "INTEGER"),
            ("daily_metrics", "training_readiness_level", "TEXT"),
            ("daily_metrics", "recovery_time_hours", "INTEGER"),
            ("daily_metrics", "acute_load", "REAL"),
            ("activities", "aerobic_te", "REAL"),
            ("activities", "anaerobic_te", "REAL"),
            ("activities", "training_load", "REAL"),
        ]
        for table, column, col_type in new_columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except sqlite3.OperationalError:
                pass  # Column already exists

    @staticmethod
    def _migrate_v3(conn: sqlite3.Connection) -> None:
        """Add training status, readiness factors, VO2 max to daily_metrics."""
        new_columns = [
            ("daily_metrics", "readiness_feedback", "TEXT"),
            ("daily_metrics", "readiness_sleep_factor", "TEXT"),
            ("daily_metrics", "readiness_hrv_factor", "TEXT"),
            ("daily_metrics", "readiness_recovery_factor", "TEXT"),
            ("daily_metrics", "readiness_acwr_factor", "TEXT"),
            ("daily_metrics", "readiness_stress_factor", "TEXT"),
            ("daily_metrics", "training_status", "TEXT"),
            ("daily_metrics", "acwr_ratio", "REAL"),
            ("daily_metrics", "chronic_load", "REAL"),
            ("daily_metrics", "load_balance", "TEXT"),
            ("daily_metrics", "vo2max_running", "REAL"),
            ("daily_metrics", "vo2max_cycling", "REAL"),
        ]
        for table, column, col_type in new_columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except sqlite3.OperationalError:
                pass

    @staticmethod
    def _migrate_v4(conn: sqlite3.Connection) -> None:
        """Deep Garmin extraction: sleep stages, BB dynamics, stress, HRV detail, movement, concerns."""
        from .migrate_v4 import DAILY_METRICS_NEW_COLUMNS, ACTIVITIES_NEW_COLUMNS, NEW_TABLES_SQL
        for table, cols in [("daily_metrics", DAILY_METRICS_NEW_COLUMNS), ("activities", ACTIVITIES_NEW_COLUMNS)]:
            for col_name, col_type in cols:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    pass
        conn.executescript(NEW_TABLES_SQL)

    @staticmethod
    def _migrate_v5(conn: sqlite3.Connection) -> None:
        """Store extracted gym-set weights in pounds for local gym machine numbers."""
        for table in ("gym_sets", "manual_gym_sets"):
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            if "weight_lb" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN weight_lb REAL")
            if "weight_kg" in cols:
                conn.execute(
                    f"""UPDATE {table}
                        SET weight_lb = ROUND(weight_kg * 2.2046226218, 2)
                        WHERE weight_lb IS NULL AND weight_kg IS NOT NULL"""
                )

    @staticmethod
    def _migrate_v6(conn: sqlite3.Connection) -> None:
        """Insights store: discovered patterns with evidence and a status lifecycle."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                discovered_date TEXT NOT NULL,
                category TEXT NOT NULL,
                statement TEXT NOT NULL,
                evidence_json TEXT,
                status TEXT NOT NULL DEFAULT 'validated',
                surfaced_date TEXT,
                adopted_rule_ref TEXT
            )
            """
        )

    # -- Daily Metrics --

    def upsert_daily_metrics(self, metrics: dict[str, Any]) -> None:
        # Build dynamic column list from metrics keys vs known columns
        all_columns = [
            "date", "hrv_weekly_avg", "hrv_last_night", "sleep_duration_min",
            "sleep_score", "sleep_start", "sleep_end",
            "body_battery_am", "stress_avg", "resting_hr", "spo2_avg",
            "training_readiness_score", "training_readiness_level",
            "recovery_time_hours", "acute_load",
            "readiness_feedback", "readiness_sleep_factor", "readiness_hrv_factor",
            "readiness_recovery_factor", "readiness_acwr_factor", "readiness_stress_factor",
            "training_status", "acwr_ratio", "chronic_load", "load_balance",
            "vo2max_running", "vo2max_cycling",
            # v4 columns
            "sleep_deep_min", "sleep_light_min", "sleep_rem_min", "sleep_awake_min",
            "sleep_score_deep", "sleep_score_rem", "sleep_score_light",
            "sleep_score_restlessness", "sleep_score_duration", "sleep_score_awake_count",
            "bb_at_wake", "bb_highest", "bb_lowest", "bb_drained",
            "bb_sleep_charge", "bb_most_recent", "bb_feedback",
            "stress_rest_pct", "stress_low_pct", "stress_medium_pct", "stress_high_pct",
            "respiration_avg", "respiration_high", "respiration_low",
            "hrv_range_low", "hrv_range_high", "hrv_status", "hrv_reading_count",
            "steps", "active_minutes", "sedentary_hours",
            "intensity_minutes_vigorous", "intensity_minutes_moderate", "floors_ascended",
            "menstrual_phase", "menstrual_day_of_cycle",
            "endurance_score", "restless_moments",
        ]
        # Always include raw_json separately
        present_cols = [c for c in all_columns if c in metrics or c == "date"]
        col_names = ", ".join(present_cols) + ", raw_json"
        placeholders = ", ".join(["?"] * len(present_cols)) + ", ?"
        update_clauses = ", ".join(
            f"{c}=COALESCE(excluded.{c}, {c})" for c in present_cols if c != "date"
        ) + ", raw_json=excluded.raw_json"

        values = [metrics.get(c) for c in present_cols]
        values.append(
            json.dumps(metrics.get("raw"), ensure_ascii=False)
            if metrics.get("raw") else None
        )

        with self._connection() as conn:
            conn.execute(
                f"""INSERT INTO daily_metrics ({col_names})
                    VALUES ({placeholders})
                    ON CONFLICT(date) DO UPDATE SET {update_clauses}""",
                values,
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
        all_columns = [
            "id", "date", "type", "duration_min", "avg_hr", "max_hr", "calories",
            "aerobic_te", "anaerobic_te", "training_load",
            "summary_json", "fit_file_path",
            # v4 columns
            "activity_name", "weather_temp_c", "weather_condition",
            "weather_wind_kmh", "weather_humidity",
            "elevation_gain", "elevation_loss", "max_elevation",
            "hr_zone1_sec", "hr_zone2_sec", "hr_zone3_sec", "hr_zone4_sec", "hr_zone5_sec",
            "distance_m", "avg_speed", "max_speed",
            "training_effect_label", "start_time",
        ]
        present_cols = [c for c in all_columns if c in activity or c in ("id", "date", "type")]
        col_names = ", ".join(present_cols) + ", raw_json"
        placeholders = ", ".join(["?"] * len(present_cols)) + ", ?"
        update_clauses = ", ".join(
            f"{c}=COALESCE(excluded.{c}, {c})" for c in present_cols if c != "id"
        ) + ", raw_json=excluded.raw_json"

        values = [activity.get(c) for c in present_cols]
        values.append(
            json.dumps(activity.get("raw"), ensure_ascii=False)
            if activity.get("raw") else None
        )

        with self._connection() as conn:
            conn.execute(
                f"""INSERT INTO activities ({col_names})
                    VALUES ({placeholders})
                    ON CONFLICT(id) DO UPDATE SET {update_clauses}""",
                values,
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
                   (activity_id, set_number, exercise, reps, weight_lb,
                    peak_hr, recovery_hr, rest_duration_sec)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        str(activity_id),
                        s["set_number"],
                        s.get("exercise"),
                        s.get("reps"),
                        s.get("weight_lb"),
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
            base_sets = [dict(r) for r in rows]
            manual_rows = conn.execute(
                "SELECT * FROM manual_gym_sets WHERE activity_id = ? ORDER BY id",
                (str(activity_id),),
            ).fetchall()

            next_set_number = (
                max((set_row.get("set_number") or 0) for set_row in base_sets) + 1
                if base_sets else 1
            )
            manual_sets: list[dict[str, Any]] = []
            for manual_row in manual_rows:
                manual = dict(manual_row)
                set_count = manual.get("set_count") or 1
                for offset in range(set_count):
                    manual_sets.append({
                        "id": f"manual-{manual['id']}-{offset + 1}",
                        "activity_id": str(activity_id),
                        "set_number": next_set_number + len(manual_sets),
                        "exercise": manual.get("exercise"),
                        "reps": manual.get("reps"),
                        "weight_lb": manual.get("weight_lb"),
                        "peak_hr": None,
                        "recovery_hr": None,
                        "rest_duration_sec": None,
                        "source": "manual",
                        "note": manual.get("note"),
                    })

            return base_sets + manual_sets

    def insert_manual_gym_sets(
        self, activity_id: str, entries: list[dict[str, Any]], note: str | None = None
    ) -> int:
        timestamp = datetime.now().isoformat(timespec="seconds")
        rows = []
        for entry in entries:
            exercise = str(entry.get("exercise") or "").strip()
            if exercise == "":
                continue
            rows.append((
                str(activity_id),
                exercise,
                entry.get("reps"),
                entry.get("weight_lb"),
                int(entry.get("sets") or entry.get("set_count") or 1),
                note or entry.get("note"),
                timestamp,
            ))
        if not rows:
            return 0
        with self._connection() as conn:
            conn.executemany(
                """INSERT INTO manual_gym_sets
                   (activity_id, exercise, reps, weight_lb, set_count, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return len(rows)

    def insert_training_feedback(
        self,
        activity_id: str,
        *,
        rpe: float | None = None,
        pain_area: str | None = None,
        pain_level: int | None = None,
        menstrual_symptoms: str | None = None,
        notes: str | None = None,
    ) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """INSERT INTO training_feedback
                   (activity_id, rpe, pain_area, pain_level, menstrual_symptoms, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(activity_id),
                    rpe,
                    pain_area,
                    pain_level,
                    menstrual_symptoms,
                    notes,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return int(cursor.lastrowid)

    def get_training_feedback(self, activity_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT * FROM training_feedback
                   WHERE activity_id = ?
                   ORDER BY id DESC
                   LIMIT 1""",
                (str(activity_id),),
            ).fetchone()
            return dict(row) if row else None

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

    def get_notifications_since(self, since_date: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE timestamp >= ? ORDER BY timestamp",
                (since_date,),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Insights --

    def insert_insight(
        self,
        key: str,
        category: str,
        statement: str,
        evidence: dict[str, Any] | None,
        status: str = "validated",
    ) -> bool:
        """Insert if key is new; returns True when a row was created."""
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO insights"
                " (key, discovered_date, category, statement, evidence_json, status)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    str(date.today()),
                    category,
                    statement,
                    json.dumps(evidence) if evidence is not None else None,
                    status,
                ),
            )
            return cursor.rowcount > 0

    def get_insights(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM insights"
        parameters: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY discovered_date ASC, id ASC"
        with self._connection() as conn:
            return [dict(row) for row in conn.execute(query, parameters).fetchall()]

    def mark_insight_surfaced(self, insight_id: int) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE insights SET status = 'surfaced', surfaced_date = ? WHERE id = ?",
                (str(date.today()), insight_id),
            )

    def mark_insight_adopted(self, insight_id: int, rule_ref: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE insights SET status = 'adopted', adopted_rule_ref = ? WHERE id = ?",
                (rule_ref, insight_id),
            )

    def mark_insight_dismissed(self, insight_id: int) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE insights SET status = 'dismissed' WHERE id = ?",
                (insight_id,),
            )

    def refresh_insight_evidence(
        self, key: str, statement: str, evidence: dict[str, Any] | None
    ) -> bool:
        """Update statement/evidence for an existing insight, preserving its
        status and dates. Returns False when the key does not exist."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE insights SET statement = ?, evidence_json = ? WHERE key = ?",
                (statement, json.dumps(evidence) if evidence is not None else None, key),
            )
            return cursor.rowcount > 0

    def save_conversation(self, chat_id: str, messages_json: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO conversation_state (chat_id, messages_json, updated_at) "
                "VALUES (?, ?, ?)",
                (chat_id, messages_json, datetime.now().isoformat()),
            )

    def load_conversation(self, chat_id: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT messages_json FROM conversation_state WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return row["messages_json"] if row else None

    # -- Concerns --

    def upsert_concern(self, concern: str, impact: str | None = None,
                       sport_affected: str | None = None, source: str = "user") -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """INSERT INTO concerns (created_date, concern, impact, sport_affected, source)
                   VALUES (date('now'), ?, ?, ?, ?)""",
                (concern, impact, sport_affected, source),
            )
            return cursor.lastrowid

    def get_active_concerns(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM concerns WHERE status = 'active' ORDER BY created_date DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def resolve_concern(self, concern_id: int) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE concerns SET status = 'resolved', resolved_date = date('now') WHERE id = ?",
                (concern_id,),
            )

    # -- Basketball Corrections --

    def get_basketball_correction(self, activity_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM basketball_corrections WHERE activity_id = ?",
                (activity_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_corrected_load(self, activity_id: str, garmin_load: float) -> float:
        """Return corrected training load for basketball (or original for other sports)."""
        correction = self.get_basketball_correction(activity_id)
        if correction is not None:
            return correction["estimated_load"]
        return garmin_load

    # -- Extended Queries --

    def get_sleep_stages(self, days: int = 7) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT date, sleep_duration_min, sleep_deep_min, sleep_light_min,
                          sleep_rem_min, sleep_awake_min, sleep_score,
                          sleep_score_deep, sleep_score_rem, sleep_score_restlessness,
                          restless_moments, bb_sleep_charge
                   FROM daily_metrics
                   WHERE sleep_deep_min IS NOT NULL
                   ORDER BY date DESC LIMIT ?""",
                (days,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_bb_dynamics(self, days: int = 7) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT date, body_battery_am, bb_at_wake, bb_highest, bb_lowest,
                          bb_drained, bb_sleep_charge, bb_most_recent, bb_feedback
                   FROM daily_metrics
                   WHERE bb_at_wake IS NOT NULL
                   ORDER BY date DESC LIMIT ?""",
                (days,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_readiness_factors(self, days: int = 7) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT date, training_readiness_score, training_readiness_level,
                          readiness_feedback, readiness_sleep_factor, readiness_hrv_factor,
                          readiness_recovery_factor, readiness_acwr_factor, readiness_stress_factor,
                          recovery_time_hours
                   FROM daily_metrics
                   WHERE training_readiness_score IS NOT NULL
                   ORDER BY date DESC LIMIT ?""",
                (days,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stress_breakdown(self, days: int = 7) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT date, stress_avg, stress_rest_pct, stress_low_pct,
                          stress_medium_pct, stress_high_pct
                   FROM daily_metrics
                   WHERE stress_rest_pct IS NOT NULL
                   ORDER BY date DESC LIMIT ?""",
                (days,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_movement_summary(self, days: int = 7) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT date, steps, active_minutes, sedentary_hours,
                          intensity_minutes_vigorous, intensity_minutes_moderate,
                          floors_ascended
                   FROM daily_metrics
                   WHERE steps IS NOT NULL
                   ORDER BY date DESC LIMIT ?""",
                (days,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_corrected_weekly_load(self, days: int = 7) -> float:
        """Get total training load for the past N days, with basketball corrections applied."""
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT a.id, a.type, a.training_load, a.date,
                          bc.estimated_load as corrected_load
                   FROM activities a
                   LEFT JOIN basketball_corrections bc ON a.id = bc.activity_id
                   WHERE a.date >= date('now', ? || ' days')
                   ORDER BY a.date DESC""",
                (f"-{days}",),
            ).fetchall()
            total = 0.0
            for r in rows:
                if r["type"] == "basketball" and r["corrected_load"] is not None:
                    total += r["corrected_load"]
                elif r["training_load"] is not None:
                    total += r["training_load"]
            return total
