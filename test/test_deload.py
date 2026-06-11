from datetime import date, timedelta

from src.ai.deload import deload_check


def _day(offset):
    return str(date.today() - timedelta(days=offset))


def _seed(db, *, week_loads, hrv7, hrv28):
    """week_loads[0] = most recent week. One activity per day carrying the load."""
    for week_index, weekly_total in enumerate(week_loads):
        for day_in_week in range(7):
            offset = week_index * 7 + day_in_week
            db.upsert_activity({
                "id": f"a{offset}", "date": _day(offset), "type": "strength",
                "duration_min": 60, "training_load": weekly_total / 7,
            })
    for offset in range(28):
        value = hrv7 if offset < 7 else hrv28
        db.upsert_daily_metrics({"date": _day(offset), "hrv_last_night": value,
                                 "training_readiness_score": 70})


def test_fires_on_rising_load_and_degraded_hrv(db):
    _seed(db, week_loads=[900, 750, 600, 450], hrv7=54.0, hrv28=60.0)
    result = deload_check(db)
    assert result is not None
    assert result["weekly_loads"][0] > result["weekly_loads"][1]


def test_silent_when_load_flat(db):
    _seed(db, week_loads=[600, 610, 590, 600], hrv7=54.0, hrv28=60.0)
    assert deload_check(db) is None


def test_silent_when_recovery_healthy(db):
    _seed(db, week_loads=[900, 750, 600, 450], hrv7=60.0, hrv28=60.0)
    assert deload_check(db) is None
