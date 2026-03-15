from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from .client import GarminClient
from .fit_parser import parse_gym_session, parse_ski_session
from ..db.models import Database

logger = logging.getLogger(__name__)


class GarminSync:
    def __init__(self, client: GarminClient, db: Database, data_dir: Path) -> None:
        self.client = client
        self.db = db
        self.fit_dir = data_dir / "fit_files"
        self.fit_dir.mkdir(parents=True, exist_ok=True)

    def sync_daily_metrics(self, target_date: date | None = None) -> dict[str, Any]:
        from datetime import timedelta
        target_date = target_date or date.today()
        logger.info("Syncing daily metrics for %s", target_date)

        metrics = self.client.get_daily_metrics(target_date)

        has_data = any(
            metrics.get(k) is not None
            for k in ("hrv_last_night", "sleep_duration_min", "resting_hr")
        )

        # If today has no data, try yesterday (Garmin may not have processed yet)
        if not has_data and target_date == date.today():
            yesterday = target_date - timedelta(days=1)
            logger.info("No data for today, trying yesterday (%s)", yesterday)
            metrics = self.client.get_daily_metrics(yesterday)
            has_data = any(
                metrics.get(k) is not None
                for k in ("hrv_last_night", "sleep_duration_min", "resting_hr")
            )

        if has_data:
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
            logger.warning("No valid metrics for %s or yesterday, skipping", target_date)

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

            self.db.upsert_activity(activity)

            if fit_path is not None:
                self._parse_and_store_fit(activity_id, activity["type"], fit_path)
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
