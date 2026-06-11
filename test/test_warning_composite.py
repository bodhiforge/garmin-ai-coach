from src.ai.warnings import health_warning


def _seed_baseline(db, days=28):
    for day_number in range(1, days + 1):
        db.upsert_daily_metrics({
            "date": f"2026-05-{day_number:02d}",
            "respiration_avg": 14.0,
            "resting_hr": 52.0,
            "hrv_last_night": 60.0,
            "sleep_score": 80.0,
        })


def test_fires_when_two_signals_deviate(db):
    _seed_baseline(db)
    db.upsert_daily_metrics({
        "date": "2026-06-10",
        "respiration_avg": 17.5,   # well above baseline
        "resting_hr": 58.0,        # well above baseline
        "hrv_last_night": 59.0,    # normal
        "sleep_score": 78.0,       # normal
    })
    warning = health_warning(db)
    assert warning is not None
    assert set(warning["fired_signals"]) == {"respiration_avg", "resting_hr"}


def test_silent_when_single_signal_deviates(db):
    _seed_baseline(db)
    db.upsert_daily_metrics({
        "date": "2026-06-10",
        "respiration_avg": 17.5,
        "resting_hr": 52.5,
        "hrv_last_night": 60.5,
        "sleep_score": 81.0,
    })
    assert health_warning(db) is None
