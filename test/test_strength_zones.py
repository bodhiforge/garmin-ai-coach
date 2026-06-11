from src.ai.strength_profile import rep_zone_distribution, rest_interval_analysis

from test.conftest import make_set, seed_strength_activity


def test_rep_zones_all_hypertrophy(db):
    seed_strength_activity(db, "a1", "2026-06-01", [
        make_set("Romanian Deadlift", 10, 45.0),
        make_set("Lat Pulldown", 12, 70.0),
    ])
    zones = rep_zone_distribution(db, days=90)
    assert zones["strength_pct"] == 0.0
    assert zones["hypertrophy_pct"] == 100.0
    assert zones["total_sets"] == 2


def test_rest_flags_rushed_compounds(db):
    seed_strength_activity(db, "a1", "2026-06-01", [
        make_set("Romanian Deadlift", 10, 45.0, rest_sec=45),
        make_set("Romanian Deadlift", 10, 45.0, rest_sec=50),
        make_set("Lateral Raise", 12, 5.0, rest_sec=40),
    ])
    rest = rest_interval_analysis(db, days=90)
    assert rest["compound_median_sec"] == 47.5
    assert rest["rushed_compounds"] is True
