from src.ai.discovery import _sleep_start_minutes, discover_patterns


def test_sleep_start_normalization_crosses_midnight():
    assert _sleep_start_minutes("23:30") == 330      # 5.5h after 18:00
    assert _sleep_start_minutes("02:56") == 536      # next-day 02:56
    assert _sleep_start_minutes("18:00") == 0


def _seed_night(db, day, start, deep_min, score):
    db.upsert_daily_metrics({"date": day, "sleep_start": start,
                             "sleep_deep_min": deep_min, "sleep_score": score})


def test_late_night_cost_detected(db):
    # 16 normal nights (~01:00, deep 75) + 9 late nights (~03:00, deep 45).
    for index in range(16):
        _seed_night(db, f"2026-05-{index + 1:02d}", "01:00", 75, 80)
    for index in range(9):
        _seed_night(db, f"2026-06-{index + 1:02d}", "03:00", 45, 65)
    keys = {finding["key"] for finding in discover_patterns(db)}
    assert "sleep.late_night_deep_cost" in keys


def test_no_finding_when_bedtime_uniform(db):
    for index in range(25):
        _seed_night(db, f"2026-05-{index + 1:02d}", "01:00", 70 + index % 5, 78)
    keys = {finding["key"] for finding in discover_patterns(db)}
    assert "sleep.late_night_deep_cost" not in keys
