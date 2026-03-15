from __future__ import annotations

import logging
import zipfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from garminconnect import Garmin

logger = logging.getLogger(__name__)

GARTH_HOME = Path.home() / ".garth"

ACTIVITY_TYPE_MAP = {
    "running": "running",
    "cycling": "cycling",
    "strength_training": "strength",
    "resort_skiing_snowboarding": "skiing",
    "resort_snowboarding": "skiing",
    "backcountry_skiing_snowboarding": "skiing",
    "backcountry_snowboarding": "skiing",
    "hiking": "hiking",
    "walking": "walking",
    "swimming": "swimming",
    "yoga": "yoga",
}


class GarminClient:
    def __init__(self, email: str, password: str) -> None:
        self.email = email
        self.password = password
        self.client = Garmin(email, password)
        self._login()

    def _login(self) -> None:
        try:
            self.client.garth.load(str(GARTH_HOME))
            # Verify session is valid by fetching display name
            self.client.display_name = self.client.garth.profile["displayName"]
            logger.info("Logged in with saved session (user: %s)", self.client.display_name)
        except Exception:
            logger.info("Saved session invalid, logging in with credentials")
            self.client = Garmin(self.email, self.password)
            self.client.login()
            self.client.garth.dump(str(GARTH_HOME))
            logger.info("Login successful, session saved (user: %s)", self.client.display_name)

    def get_daily_metrics(self, target_date: date | None = None) -> dict[str, Any]:
        target_date = target_date or date.today()
        date_str = target_date.isoformat()

        stats = self.client.get_stats_and_body(date_str)
        hrv = self._get_hrv(date_str)
        sleep = self._get_sleep(date_str)
        stress = self._get_stress(date_str)
        body_battery = self._get_body_battery(target_date)

        return {
            "date": date_str,
            "hrv_weekly_avg": _parse_hrv(hrv, "weeklyAvg"),
            "hrv_last_night": _parse_hrv(hrv, "lastNightAvg"),
            "sleep_duration_min": _parse_sleep_duration(sleep),
            "sleep_score": _parse_sleep_score(sleep),
            "body_battery_am": _parse_body_battery_morning(body_battery),
            "stress_avg": stress.get("avgStressLevel") if stress else None,
            "resting_hr": stats.get("restingHeartRate"),
            "spo2_avg": stats.get("averageSpo2"),
            "raw": {
                "stats": stats,
                "hrv": hrv,
                "sleep": sleep,
                "stress": stress,
                "body_battery": body_battery,
            },
        }

    def _get_hrv(self, date_str: str) -> dict[str, Any] | None:
        try:
            return self.client.get_hrv_data(date_str)
        except Exception as e:
            logger.warning("Failed to get HRV data: %s", e)
            return None

    def _get_sleep(self, date_str: str) -> dict[str, Any] | None:
        try:
            return self.client.get_sleep_data(date_str)
        except Exception as e:
            logger.warning("Failed to get sleep data: %s", e)
            return None

    def _get_stress(self, date_str: str) -> dict[str, Any] | None:
        try:
            return self.client.get_all_day_stress(date_str)
        except Exception as e:
            logger.warning("Failed to get stress data: %s", e)
            return None

    def _get_body_battery(self, target_date: date) -> list[dict] | None:
        try:
            return self.client.get_body_battery(
                target_date.isoformat(), target_date.isoformat()
            )
        except Exception as e:
            logger.warning("Failed to get body battery: %s", e)
            return None

    def get_training_readiness(self, target_date: date | None = None) -> dict[str, Any] | None:
        target_date = target_date or date.today()
        try:
            data = self.client.get_training_readiness(target_date.isoformat())
            if not data:
                return None
            # API returns a list of readings; take the most recent one
            latest = data[0]
            return {
                "score": latest.get("score"),
                "level": latest.get("level"),
                "recovery_time_hours": latest.get("recoveryTime"),
                "acute_load": latest.get("acuteLoad"),
                "sleep_score_factor": latest.get("sleepScoreFactorFeedback"),
                "hrv_factor": latest.get("hrvFactorFeedback"),
                "recovery_factor": latest.get("recoveryTimeFactorFeedback"),
                "acwr_factor": latest.get("acwrFactorFeedback"),
            }
        except Exception as e:
            logger.warning("Failed to get training readiness: %s", e)
            return None

    def get_recent_activities(self, limit: int = 10) -> list[dict[str, Any]]:
        try:
            raw_activities = self.client.get_activities(0, limit)
        except Exception as e:
            logger.error("Failed to get activities: %s", e)
            return []

        return [_normalize_activity(a) for a in raw_activities]

    def download_fit_file(self, activity_id: str, output_dir: Path) -> Path | None:
        try:
            zip_data = self.client.download_activity(
                activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
            )
            zip_buffer = BytesIO(zip_data)
            output_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_buffer) as zf:
                fit_names = [n for n in zf.namelist() if n.endswith(".fit")]
                if not fit_names:
                    logger.warning("No FIT file found in zip for activity %s", activity_id)
                    return None

                fit_name = fit_names[0]
                output_path = output_dir / f"{activity_id}.fit"
                with open(output_path, "wb") as f:
                    f.write(zf.read(fit_name))

                logger.info("Downloaded FIT file: %s", output_path)
                return output_path

        except Exception as e:
            logger.error("Failed to download FIT for activity %s: %s", activity_id, e)
            return None


def _normalize_activity(raw: dict[str, Any]) -> dict[str, Any]:
    activity_type_key = raw.get("activityType", {}).get("typeKey", "unknown")
    return {
        "id": str(raw.get("activityId", "")),
        "date": raw.get("startTimeLocal", "")[:10],
        "type": ACTIVITY_TYPE_MAP.get(activity_type_key, activity_type_key),
        "duration_min": round(raw.get("duration", 0) / 60, 1),
        "avg_hr": raw.get("averageHR"),
        "max_hr": raw.get("maxHR"),
        "calories": raw.get("calories"),
        "aerobic_te": raw.get("aerobicTrainingEffect"),
        "anaerobic_te": raw.get("anaerobicTrainingEffect"),
        "training_load": raw.get("activityTrainingLoad"),
        "raw": raw,
    }


def _parse_hrv(hrv: dict | None, key: str) -> float | None:
    if hrv is None:
        return None
    # HRV data may be nested under hrvSummary
    summary = hrv.get("hrvSummary", hrv)
    return summary.get(key)


def _parse_sleep_duration(sleep: dict | None) -> int | None:
    if sleep is None:
        return None
    daily = sleep.get("dailySleepDTO", {})
    seconds = daily.get("sleepTimeSeconds") or daily.get("sleepTimeInSeconds")
    if seconds is not None:
        return round(seconds / 60)
    return None


def _parse_sleep_score(sleep: dict | None) -> int | None:
    if sleep is None:
        return None
    daily = sleep.get("dailySleepDTO", {})
    return daily.get("sleepScores", {}).get("overall", {}).get("value")





def _parse_body_battery_morning(battery: list[dict] | None) -> int | None:
    if not battery:
        return None
    first_day = battery[0] if battery else {}
    # charged value at start of day approximates morning battery
    return first_day.get("charged")
