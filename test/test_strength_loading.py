from src.ai.strength_profile import load_strength_sets

from test.conftest import make_set, seed_strength_activity


def test_excludes_non_lift_entries(db):
    seed_strength_activity(db, "a1", "2026-06-01", [
        make_set("Lat Pulldown", 12, 70.0),
        make_set("Treadmill", 195, None),
        make_set("Stretch Hip Flexor And Quad", 1, None),
    ])
    rows = load_strength_sets(db, days=30)
    assert [row["exercise"] for row in rows] == ["Lat Pulldown"]


def test_rows_carry_session_date(db):
    seed_strength_activity(db, "a1", "2026-06-01", [make_set("Romanian Deadlift", 10, 45.0)])
    rows = load_strength_sets(db, days=30)
    assert rows[0]["date"] == "2026-06-01"
    assert rows[0]["weight_lb"] == 45.0
