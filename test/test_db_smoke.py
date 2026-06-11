from test.conftest import make_set, seed_strength_activity


def test_activity_roundtrip(db):
    seed_strength_activity(db, "a1", "2026-06-01", [make_set("Lat Pulldown", 12, 70.0)])
    activities = db.get_recent_activities(days=3650, activity_type="strength")
    assert len(activities) == 1
    sets = db.get_gym_sets("a1")
    assert sets[0]["exercise"] == "Lat Pulldown"
    assert sets[0]["weight_lb"] == 70.0
