from src.ai.observations import detect_observations

from test.conftest import make_set, seed_strength_activity


def _seed_low_readiness_training(db):
    """7 days of metrics, 2 LOW-readiness days, trained on both — trips _rest_compliance."""
    for day_number in range(1, 8):
        day = f"2026-06-{day_number:02d}"
        readiness = 20 if day_number <= 2 else 80
        db.upsert_daily_metrics({
            "date": day,
            "training_readiness_score": readiness,
            "hrv_last_night": 60,
        })
    for day_number in (1, 2):
        day = f"2026-06-{day_number:02d}"
        seed_strength_activity(db, f"a{day_number}", day, [make_set("Lat Pulldown", 12, 70.0)])


def test_observations_persist_to_insights_table(db, tmp_path):
    _seed_low_readiness_training(db)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    found = detect_observations(db, memory_dir)
    if not found:
        # Seeding didn't trip any detector — fix the seed, not the assert.
        raise AssertionError("expected at least one observation from seeded data")
    stored = db.get_insights()
    assert len(stored) >= 1
    assert all(row["category"] == "observation" for row in stored)
    assert (memory_dir / "observations.md").exists()
