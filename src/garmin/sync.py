from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from .client import GarminClient
from ..db.migrate_v4 import extract_daily_metrics_from_raw, extract_activity_from_raw
from .fit_parser import parse_gym_session, parse_ski_session
from ..db.models import Database

logger = logging.getLogger(__name__)


class GarminSync:
    def __init__(self, client: GarminClient, db: Database, data_dir: Path) -> None:
        self.client = client
        self.db = db
        self.data_dir = data_dir
        self.fit_dir = data_dir / "fit_files"
        self.fit_dir.mkdir(parents=True, exist_ok=True)

    def sync_daily_metrics(self, target_date: date | None = None) -> dict[str, Any]:
        from datetime import timedelta
        target_date = target_date or date.today()
        logger.info("Syncing daily metrics for %s", target_date)

        metrics = self.client.get_daily_metrics(target_date)

        # Merge training readiness (full breakdown)
        readiness = self.client.get_training_readiness_full(target_date)
        if readiness is not None:
            metrics["training_readiness_score"] = readiness["score"]
            metrics["training_readiness_level"] = readiness["level"]
            metrics["recovery_time_hours"] = readiness["recovery_time_hours"]
            metrics["acute_load"] = readiness["acute_load"]
            metrics["readiness_feedback"] = readiness["feedback"]
            metrics["readiness_sleep_factor"] = readiness["sleep_factor"]
            metrics["readiness_hrv_factor"] = readiness["hrv_factor"]
            metrics["readiness_recovery_factor"] = readiness["recovery_factor"]
            metrics["readiness_acwr_factor"] = readiness["acwr_factor"]
            metrics["readiness_stress_factor"] = readiness["stress_factor"]
        else:
            # Fallback to simple readiness
            readiness_simple = self.client.get_training_readiness(target_date)
            if readiness_simple is not None:
                metrics["training_readiness_score"] = readiness_simple["score"]
                metrics["training_readiness_level"] = readiness_simple["level"]
                metrics["recovery_time_hours"] = readiness_simple["recovery_time_hours"]
                metrics["acute_load"] = readiness_simple["acute_load"]

        # Merge training status (ACWR, load balance, VO2 max)
        status = self.client.get_training_status(target_date)
        if status is not None:
            metrics["training_status"] = status.get("training_status_feedback")
            metrics["acwr_ratio"] = status.get("acwr_ratio")
            metrics["chronic_load"] = status.get("chronic_load")
            metrics["load_balance"] = status.get("load_balance_feedback")
            metrics["vo2max_running"] = status.get("vo2max_running")
            metrics["vo2max_cycling"] = status.get("vo2max_cycling")

        # Merge menstrual cycle data
        menstrual = self.client.get_menstrual_data(target_date)
        if menstrual is not None:
            metrics["menstrual_phase"] = menstrual.get("phase")
            metrics["menstrual_day_of_cycle"] = menstrual.get("day_of_cycle")

        # Merge endurance score
        endurance = self.client.get_endurance_score(target_date)
        if endurance is not None:
            metrics["endurance_score"] = endurance

        has_data = any(
            metrics.get(k) is not None
            for k in ("hrv_last_night", "sleep_duration_min", "resting_hr")
        )

        if has_data:
            # Extract deep fields from raw data for new v4 columns
            if metrics.get("raw"):
                import json
                raw_json_str = json.dumps(metrics["raw"], ensure_ascii=False)
                extracted = extract_daily_metrics_from_raw(raw_json_str)
                metrics.update(extracted)
            self.db.upsert_daily_metrics(metrics)
            logger.info(
                "Synced metrics [%s]: HRV=%s, sleep=%smin, BB=%s, RHR=%s",
                metrics.get("date"),
                metrics.get("hrv_last_night"),
                metrics.get("sleep_duration_min"),
                metrics.get("body_battery_am"),
                metrics.get("resting_hr"),
            )
        else:
            logger.info("No data yet for %s", target_date)

        # Always re-sync yesterday — Garmin backfills fields (sleep times,
        # readiness) hours after the initial sync window
        if target_date == date.today():
            yesterday = target_date - timedelta(days=1)
            logger.info("Re-syncing yesterday (%s) for backfill", yesterday)
            y_metrics = self.client.get_daily_metrics(yesterday)
            y_has = any(
                y_metrics.get(k) is not None
                for k in ("hrv_last_night", "sleep_duration_min", "resting_hr")
            )
            if y_has:
                self.db.upsert_daily_metrics(y_metrics)
                logger.info(
                    "Backfilled [%s]: sleep_start=%s, sleep_end=%s",
                    y_metrics.get("date"),
                    y_metrics.get("sleep_start"),
                    y_metrics.get("sleep_end"),
                )

        # Wake-up detection: if today's sleep_end just appeared, signal it
        self._last_wake_detected = False
        if has_data and metrics.get("sleep_end") is not None:
            wake_flag = self.data_dir / ".wake_sent_today"
            today_str = date.today().isoformat()
            already_sent = wake_flag.exists() and wake_flag.read_text().strip() == today_str
            if not already_sent:
                wake_flag.write_text(today_str)
                self._last_wake_detected = True
                logger.info("Wake-up detected: sleep_end=%s. Flagged for morning push.", metrics.get("sleep_end"))

        return metrics

    def sync_activities(self, limit: int = 10) -> list[dict[str, Any]]:
        logger.info("Syncing recent activities (limit=%d)", limit)
        activities = self.client.get_recent_activities(limit)
        new_activities: list[dict[str, Any]] = []

        for activity in activities:
            activity_id = activity["id"]
            if self.db.activity_exists(activity_id):
                continue

            logger.info(
                "New activity: %s (%s) on %s",
                activity_id, activity["type"], activity["date"],
            )

            # Insert activity first (FK parent), then parse FIT
            fit_path = None
            if activity["type"] in ("strength", "skiing"):
                fit_path = self.client.download_fit_file(activity_id, self.fit_dir)
                if fit_path is not None:
                    activity["fit_file_path"] = str(fit_path)

            # Extract deep fields from raw data for new v4 columns
            if activity.get("raw"):
                import json
                raw_json_str = json.dumps(activity["raw"], ensure_ascii=False)
                extracted = extract_activity_from_raw(raw_json_str)
                activity.update(extracted)
            self.db.upsert_activity(activity)

            if fit_path is not None:
                self._parse_and_store_fit(activity_id, activity["type"], fit_path)
            # Sync weather for outdoor activities
            if activity["type"] in ("skiing", "hiking", "running", "cycling"):
                weather = self.client.get_activity_weather(activity_id)
                if weather is not None:
                    activity.update({
                        f"weather_{k}": v for k, v in weather.items() if v is not None
                    })
                    self.db.upsert_activity(activity)

            # Sync HR zones
            hr_zones = self.client.get_activity_hr_zones(activity_id)
            if hr_zones is not None:
                zone_map = {}
                for z in hr_zones:
                    zone_num = z.get("zone")
                    secs = z.get("seconds")
                    if zone_num is not None and secs is not None and 1 <= zone_num <= 5:
                        zone_map[f"hr_zone{zone_num}_sec"] = secs
                if zone_map:
                    activity.update(zone_map)
                    self.db.upsert_activity(activity)

            new_activities.append(activity)

        logger.info("Synced %d new activities", len(new_activities))
        return new_activities

    def _parse_and_store_fit(
        self, activity_id: str, activity_type: str, fit_path: Path
    ) -> None:
        try:
            if activity_type == "strength":
                sets = parse_gym_session(fit_path)
                if sets:
                    self.db.insert_gym_sets(activity_id, sets)
                    logger.info("Parsed %d gym sets for activity %s", len(sets), activity_id)

            elif activity_type == "skiing":
                runs = parse_ski_session(fit_path)
                if runs:
                    self.db.insert_ski_runs(activity_id, runs)
                    logger.info("Parsed %d ski runs for activity %s", len(runs), activity_id)

        except Exception as e:
            logger.error("Failed to parse FIT file %s: %s", fit_path, e)
