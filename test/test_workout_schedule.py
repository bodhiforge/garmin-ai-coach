from src.garmin.workout import already_pushed_today, record_push


def test_same_day_dedup(tmp_path):
    assert already_pushed_today(tmp_path, "2026-06-12") is False
    record_push(tmp_path, "2026-06-12", workout_id="123", plan_name="Lower A")
    assert already_pushed_today(tmp_path, "2026-06-12") is True
    assert already_pushed_today(tmp_path, "2026-06-13") is False
