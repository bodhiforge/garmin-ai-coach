from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from ..config import load_config
from ..db.models import Database
from ..ai.coach import AICoach

logger = logging.getLogger(__name__)

app = FastAPI(title="Garmin AI Coach", docs_url=None, redoc_url=None)

# Globals initialized on startup
db: Database | None = None
coach: AICoach | None = None
API_TOKEN: str = ""


class SetData(BaseModel):
    set_number: int
    peak_hr: int
    recovery_hr: int | None = None
    rest_duration_sec: int | None = None
    exercise: str | None = None
    reps: int | None = None
    weight_kg: float | None = None


class CoachingRequest(BaseModel):
    current_hr: int
    session_sets: list[SetData]
    elapsed_min: float | None = None


class CoachingResponse(BaseModel):
    advice: str
    target_hr: int
    fatigue_pct: int  # 0-100


def verify_token(x_api_token: str = Header()) -> None:
    if x_api_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


@app.on_event("startup")
def startup() -> None:
    global db, coach, API_TOKEN

    config_path = os.environ.get("GARMIN_COACH_CONFIG", "/opt/garmin-coach/config.yaml")
    config = load_config(config_path)

    db = Database(config.data_dir / "garmin.db")
    coach = AICoach(
        api_key=config.llm.api_key,
        model=config.llm.model,
        db=db,
        base_url=config.llm.base_url,
        data_dir=config.data_dir,
    )
    API_TOKEN = os.environ.get("GARMIN_COACH_API_TOKEN", "")
    if not API_TOKEN:
        logger.warning("No API_TOKEN set — API is unprotected!")


@app.post("/api/coaching", response_model=CoachingResponse)
def get_coaching(req: CoachingRequest, x_api_token: str = Header()) -> CoachingResponse:
    verify_token(x_api_token)

    # Build session context
    session_lines = []
    for s in req.session_sets:
        line = f"Set {s.set_number}: peak HR {s.peak_hr}"
        if s.exercise is not None:
            line = f"Set {s.set_number} ({s.exercise}): peak HR {s.peak_hr}"
        if s.recovery_hr is not None:
            line += f", recovery HR {s.recovery_hr}"
        if s.rest_duration_sec is not None:
            line += f", rest {s.rest_duration_sec}s"
        if s.reps is not None and s.weight_kg is not None:
            line += f", {s.reps} reps × {s.weight_kg}kg"
        session_lines.append(line)

    session_data = (
        f"Current HR: {req.current_hr}\n"
        f"Sets completed: {len(req.session_sets)}\n"
        f"Elapsed: {req.elapsed_min or '?'} min\n\n"
        + "\n".join(session_lines)
    )

    # Get today's metrics
    today_metrics = db.get_daily_metrics(str(date.today()))
    metrics_str = _format_metrics(today_metrics) if today_metrics is not None else "No data today"

    # Get recent activities
    recent = db.get_recent_activities(days=7)
    recent_str = "\n".join(
        f"- {a['date']} {a['type']} ({a.get('duration_min', '?')}min)"
        for a in recent
    ) if recent else "No recent activities"

    # Call AI
    prompt = coach._load_prompt("realtime_coach").format(
        today_metrics=metrics_str,
        session_data=session_data,
        recent_activities=recent_str,
    )

    raw_response = coach._call_ai(prompt)

    # Parse response — expect: line 1 = advice, line 2 = target HR
    lines = raw_response.strip().split("\n")
    advice = lines[0].strip() if lines else "继续"

    # Try to extract target HR from response
    target_hr = _extract_target_hr(lines, req)

    # Calculate fatigue percentage from cardiac drift
    fatigue_pct = _calculate_fatigue(req.session_sets)

    return CoachingResponse(
        advice=advice[:60],  # Truncate for watch display
        target_hr=target_hr,
        fatigue_pct=fatigue_pct,
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


def _extract_target_hr(lines: list[str], req: CoachingRequest) -> int:
    # Try to find a number in the second line
    for line in lines[1:]:
        for word in line.split():
            cleaned = word.strip("bpmBPM:：≤<")
            if cleaned.isdigit():
                val = int(cleaned)
                if 60 <= val <= 180:
                    return val

    # Fallback: 65% of last set's peak HR
    if req.session_sets:
        last_peak = req.session_sets[-1].peak_hr
        return int(last_peak * 0.65)
    return 120


def _calculate_fatigue(sets: list[SetData]) -> int:
    if len(sets) < 2:
        return 0

    first_peak = sets[0].peak_hr
    last_peak = sets[-1].peak_hr

    if first_peak == 0:
        return 0

    # Cardiac drift: how much higher is the peak HR in later sets
    drift = (last_peak - first_peak) / first_peak * 100
    # Map drift to fatigue percentage (0-15% drift = 0-100% fatigue)
    fatigue = min(100, max(0, int(drift / 15 * 100)))
    return fatigue


def _format_metrics(metrics: dict[str, Any]) -> str:
    return (
        f"HRV: {metrics.get('hrv_last_night', '?')}ms (avg {metrics.get('hrv_weekly_avg', '?')}ms)\n"
        f"Sleep: {metrics.get('sleep_duration_min', '?')}min\n"
        f"Body Battery: {metrics.get('body_battery_am', '?')}/100\n"
        f"Resting HR: {metrics.get('resting_hr', '?')}bpm"
    )
