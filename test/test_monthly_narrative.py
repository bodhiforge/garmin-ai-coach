from src.main import _write_monthly_narrative


def test_writes_once_per_month(db, tmp_path):
    db.upsert_daily_metrics({"date": "2026-06-10", "vo2max_running": 38.0,
                             "endurance_score": 5400, "hrv_last_night": 60.0})
    target = tmp_path / "monthly-narrative.txt"

    assert _write_monthly_narrative(db, target) is True
    assert target.exists()
    # Second call within the month: suppressed via notifications cooldown.
    assert _write_monthly_narrative(db, target) is False
