from src.ai.strength_profile import movement_pattern_matrix, weekly_muscle_volume

from test.conftest import make_set, seed_strength_activity


def _seed_pull_heavy_month(db):
    # 4 weekly sessions: 6 back pull sets each, 1 chest push set each.
    for week, day in enumerate(["2026-05-16", "2026-05-23", "2026-05-30", "2026-06-06"]):
        sets = [make_set("Lat Pulldown", 12, 70.0)] * 3 + \
               [make_set("Seated Cable Row", 12, 55.0)] * 3 + \
               [make_set("Cable Crossover", 12, 23.0)]
        seed_strength_activity(db, f"w{week}", day, sets)


def test_weekly_volume_flags_low_groups(db):
    _seed_pull_heavy_month(db)
    volume = weekly_muscle_volume(db, days=28)
    assert volume["back"]["weekly_sets"] == 6.0
    assert volume["back"]["flag"] == "below_floor"   # 6 < 10
    assert volume["chest"]["weekly_sets"] == 1.0
    assert volume["quads"]["weekly_sets"] == 0.0


def test_pattern_matrix_reports_gaps(db):
    _seed_pull_heavy_month(db)
    matrix = movement_pattern_matrix(db, days=28)
    assert matrix["counts"]["pull_v"] == 12
    assert matrix["counts"]["squat"] == 0
    assert "squat" in matrix["gaps"]
    assert "hinge" in matrix["gaps"]
