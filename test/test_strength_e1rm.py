from src.ai.strength_profile import e1rm, e1rm_trend

from test.conftest import make_set, seed_strength_activity


def test_epley_formula():
    assert e1rm(100.0, 1) == 100.0
    assert round(e1rm(100.0, 10), 1) == 133.3


def _seed_sessions(db, weights):
    for index, weight in enumerate(weights):
        day = f"2026-05-{index + 1:02d}"
        seed_strength_activity(db, f"a{index}", day, [make_set("Romanian Deadlift", 10, weight)])


def test_plateau_detected_after_flat_sessions(db):
    _seed_sessions(db, [40.0, 45.0, 45.0, 45.0, 45.0])
    trend = e1rm_trend(db, days=90)["Romanian Deadlift"]
    assert trend["plateau"] is True
    assert trend["sessions"] == 5


def test_no_plateau_while_progressing(db):
    _seed_sessions(db, [40.0, 42.5, 45.0, 47.5])
    trend = e1rm_trend(db, days=90)["Romanian Deadlift"]
    assert trend["plateau"] is False
