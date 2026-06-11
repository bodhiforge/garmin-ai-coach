from __future__ import annotations

import logging
import zipfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from garminconnect import Garmin

logger = logging.getLogger(__name__)
MENSTRUAL_PHASES = {
    1: "period",
    2: "follicular",
    3: "ovulation",
    4: "luteal",
}

GARTH_HOME = Path.home() / ".garth"

ACTIVITY_TYPE_MAP = {
    "running": "running",
    "cycling": "cycling",
    "road_biking": "cycling",
    "gravel_cycling": "cycling",
    "mountain_biking": "cycling",
    "indoor_cycling": "cycling",
    "strength_training": "strength",
    "resort_skiing_snowboarding": "skiing",
    "resort_snowboarding": "skiing",
    "backcountry_skiing_snowboarding": "skiing",
    "backcountry_snowboarding": "skiing",
    "hiking": "hiking",
    "walking": "walking",
    "swimming": "swimming",
    "lap_swimming": "swimming",
    "open_water_swimming": "swimming",
    "tennis": "tennis",
    "surfing": "surfing",
    "surfing_v2": "surfing",
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

        sleep_start, sleep_end = _parse_sleep_times(sleep)

        return {
            "date": date_str,
            "hrv_weekly_avg": _parse_hrv(hrv, "weeklyAvg"),
            "hrv_last_night": _parse_hrv(hrv, "lastNightAvg"),
            "sleep_duration_min": _parse_sleep_duration(sleep),
            "sleep_score": _parse_sleep_score(sleep),
            "sleep_start": sleep_start,
            "sleep_end": sleep_end,
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

    def get_training_readiness_full(self, target_date: date | None = None) -> dict[str, Any] | None:
        """Get detailed morning training readiness with all factor breakdowns."""
        target_date = target_date or date.today()
        try:
            data = self.client.get_morning_training_readiness(target_date.isoformat())
            if not data:
                return None
            return {
                "score": data.get("score"),
                "level": data.get("level"),
                "feedback": data.get("feedbackShort"),
                "recovery_time_hours": data.get("recoveryTime"),
                "acute_load": data.get("acuteLoad"),
                "hrv_weekly_avg": data.get("hrvWeeklyAverage"),
                "sleep_score": data.get("sleepScore"),
                "sleep_factor": data.get("sleepScoreFactorFeedback"),
                "sleep_factor_pct": data.get("sleepScoreFactorPercent"),
                "recovery_factor": data.get("recoveryTimeFactorFeedback"),
                "recovery_factor_pct": data.get("recoveryTimeFactorPercent"),
                "hrv_factor": data.get("hrvFactorFeedback"),
                "hrv_factor_pct": data.get("hrvFactorPercent"),
                "acwr_factor": data.get("acwrFactorFeedback"),
                "acwr_factor_pct": data.get("acwrFactorPercent"),
                "stress_factor": data.get("stressHistoryFactorFeedback"),
                "stress_factor_pct": data.get("stressHistoryFactorPercent"),
                "sleep_history_factor": data.get("sleepHistoryFactorFeedback"),
                "sleep_history_factor_pct": data.get("sleepHistoryFactorPercent"),
            }
        except Exception as e:
            logger.warning("Failed to get morning training readiness: %s", e)
            return None

    def get_training_status(self, target_date: date | None = None) -> dict[str, Any] | None:
        """Get training status with load balance and ACWR."""
        target_date = target_date or date.today()
        try:
            data = self.client.get_training_status(target_date.isoformat())
            if not data:
                return None

            result = {}

            # Training status
            status_data = data.get("mostRecentTrainingStatus") or {}
            latest_status = status_data.get("latestTrainingStatusData") or {}
            for device_data in latest_status.values():
                if device_data is None:
                    continue
                result["training_status"] = device_data.get("trainingStatus")
                result["training_status_feedback"] = device_data.get("trainingStatusFeedbackPhrase")
                acwl = device_data.get("acuteTrainingLoadDTO", {})
                result["acwr_percent"] = acwl.get("acwrPercent")
                result["acwr_status"] = acwl.get("acwrStatus")
                result["acute_load"] = acwl.get("dailyTrainingLoadAcute")
                result["chronic_load"] = acwl.get("dailyTrainingLoadChronic")
                result["acwr_ratio"] = acwl.get("dailyAcuteChronicWorkloadRatio")
                break

            # Load balance
            balance_data = data.get("mostRecentTrainingLoadBalance") or {}
            balance_map = balance_data.get("metricsTrainingLoadBalanceDTOMap") or {}
            for device_data in balance_map.values():
                if device_data is None:
                    continue
                result["load_aerobic_low"] = device_data.get("monthlyLoadAerobicLow")
                result["load_aerobic_high"] = device_data.get("monthlyLoadAerobicHigh")
                result["load_anaerobic"] = device_data.get("monthlyLoadAnaerobic")
                result["load_balance_feedback"] = device_data.get("trainingBalanceFeedbackPhrase")
                break

            # VO2 Max
            vo2 = data.get("mostRecentVO2Max") or {}
            result["vo2max_running"] = vo2.get("generic")
            result["vo2max_cycling"] = vo2.get("cycling")

            return result
        except Exception as e:
            logger.warning("Failed to get training status: %s", e)
            return None

    def get_recent_activities(self, limit: int = 10) -> list[dict[str, Any]]:
        try:
            raw_activities = self.client.get_activities(0, limit)
        except Exception as e:
            logger.error("Failed to get activities: %s", e)
            return []

        return [_normalize_activity(a) for a in raw_activities]


    def get_menstrual_data(self, target_date: date | None = None) -> dict[str, Any] | None:
        """Get menstrual cycle phase and day for a given date."""
        target_date = target_date or date.today()
        try:
            data = self.client.get_menstrual_data_for_date(target_date.isoformat())
            if not data:
                return None
            cycle_info = data.get("daySummary") or data.get("cycleInfo", data)
            if isinstance(cycle_info, list) and cycle_info:
                cycle_info = cycle_info[0]
            if not isinstance(cycle_info, dict):
                return None
            phase = cycle_info.get("currentPhase") or cycle_info.get("phase")
            if isinstance(phase, int):
                phase = MENSTRUAL_PHASES.get(phase, str(phase))
            return {
                "phase": phase,
                "day_of_cycle": cycle_info.get("dayOfCycle") or cycle_info.get("dayInCycle"),
                "cycle_length": (
                    cycle_info.get("cycleLength")
                    or cycle_info.get("predictedCycleLength")
                ),
            }
        except Exception as e:
            logger.debug("Menstrual data not available: %s", e)
            return None

    def get_endurance_score(self, target_date: date | None = None) -> float | None:
        """Get endurance score for a given date."""
        target_date = target_date or date.today()
        try:
            data = self.client.get_endurance_score(
                target_date.isoformat(), target_date.isoformat()
            )
            if not data:
                return None
            # Navigate to the score — structure varies by API version
            if isinstance(data, dict):
                scores = data.get("enduranceScoreDTOList") or data.get("overallScores") or []
                if isinstance(scores, list) and scores:
                    return scores[-1].get("enduranceScore") or scores[-1].get("overallScore")
                return data.get("enduranceScore")
            return None
        except Exception as e:
            logger.debug("Endurance score not available: %s", e)
            return None

    def get_activity_weather(self, activity_id: str) -> dict[str, Any] | None:
        """Get weather conditions during an activity."""
        try:
            data = self.client.get_activity_weather(str(activity_id))
            if not data:
                return None
            return {
                "temp_c": data.get("temp"),
                "condition": data.get("weatherTypeDTO", {}).get("desc")
                             if isinstance(data.get("weatherTypeDTO"), dict)
                             else data.get("issueWeatherDTO", {}).get("desc"),
                "wind_kmh": data.get("windSpeed"),
                "humidity": data.get("relativeHumidity"),
            }
        except Exception as e:
            logger.debug("Activity weather not available for %s: %s", activity_id, e)
            return None

    def get_activity_hr_zones(self, activity_id: str) -> list[dict[str, Any]] | None:
        """Get heart rate zone time distribution for an activity."""
        try:
            data = self.client.get_activity_hr_in_timezones(str(activity_id))
            if not data:
                return None
            zones = []
            for zone in data:
                if isinstance(zone, dict):
                    zones.append({
                        "zone": zone.get("zoneNumber"),
                        "seconds": zone.get("secsInZone"),
                        "low_bpm": zone.get("zoneLowBoundary"),
                        "high_bpm": zone.get("zoneHighBoundary"),
                    })
            return zones if zones else None
        except Exception as e:
            logger.debug("HR zones not available for %s: %s", activity_id, e)
            return None

    def get_exercise_sets(self, activity_id: str) -> list[dict[str, Any]] | None:
        """
        Fetch per-set exercise data (name, weight, reps) for a strength activity.
        Combines Garmin's live watch detection with any post-workout edits made
        in Garmin Connect — this is the canonical source for sets.

        Weight comes from the API in grams; we convert to kg.
        Exercise names arrive UPPER_SNAKE_CASE (e.g. LAT_PULLDOWN); we humanize.
        REST frames are skipped but their duration populates the preceding set's
        rest_duration_sec.
        """
        try:
            data = self.client.get_activity_exercise_sets(str(activity_id))
            if not data or not data.get("exerciseSets"):
                return None

            raw_sets = data.get("exerciseSets") or []
            sets: list[dict[str, Any]] = []
            pending_rest_for: dict[str, Any] | None = None

            for raw in raw_sets:
                set_type = (raw.get("setType") or "").upper()
                duration = raw.get("duration")

                if set_type == "REST":
                    if pending_rest_for is not None and duration is not None:
                        pending_rest_for["rest_duration_sec"] = int(round(float(duration)))
                        pending_rest_for = None
                    continue

                if set_type != "ACTIVE":
                    continue

                exercises = raw.get("exercises") or []
                exercise_name = None
                if exercises:
                    name = exercises[0].get("name") or exercises[0].get("category")
                    if name:
                        exercise_name = str(name).replace("_", " ").title()

                weight_g = raw.get("weight")
                weight_lb = (
                    round(float(weight_g) / 453.59237, 2)
                    if weight_g not in (None, 0, 0.0)
                    else None
                )

                reps = raw.get("repetitionCount")
                set_data: dict[str, Any] = {
                    "set_number": len(sets) + 1,
                    "exercise": exercise_name,
                    "reps": int(reps) if reps is not None else None,
                    "weight_lb": weight_lb,
                    "peak_hr": None,
                    "recovery_hr": None,
                    "rest_duration_sec": None,
                }
                sets.append(set_data)
                pending_rest_for = set_data

            return sets if sets else None
        except Exception as e:
            logger.debug("Exercise sets not available for %s: %s", activity_id, e)
            return None

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





def _parse_sleep_times(sleep: dict | None) -> tuple[str | None, str | None]:
    """Extract sleep start/end as local time strings (HH:MM)."""
    if sleep is None:
        return None, None
    daily = sleep.get("dailySleepDTO", {})
    start_ms = daily.get("sleepStartTimestampLocal")
    end_ms = daily.get("sleepEndTimestampLocal")
    if start_ms is None or end_ms is None:
        return None, None
    from datetime import datetime
    start = datetime.utcfromtimestamp(start_ms / 1000).strftime("%H:%M")
    end = datetime.utcfromtimestamp(end_ms / 1000).strftime("%H:%M")
    return start, end


def _parse_body_battery_morning(battery: list[dict] | None) -> int | None:
    if not battery:
        return None
    first_day = battery[0] if battery else {}
    # charged value at start of day approximates morning battery
    return first_day.get("charged")
