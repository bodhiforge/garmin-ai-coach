from src.ai.discovery import discover_patterns


def _seed_hrv(db, day, value):
    db.upsert_daily_metrics({"date": day, "hrv_last_night": value,
                             "resting_hr": 55, "training_readiness_score": 70})


def test_activity_next_day_hrv_drop_detected(db):
    """10 basketball sessions, HRV drops ~12% the morning after each ⇒ finding."""
    for index in range(10):
        game = f"2026-04-{2 * index + 1:02d}"
        after = f"2026-04-{2 * index + 2:02d}"
        _seed_hrv(db, game, 60.0)
        _seed_hrv(db, after, 52.5)
        db.upsert_activity({"id": f"b{index}", "date": game, "type": "basketball",
                            "duration_min": 90, "training_load": 150})
    findings = discover_patterns(db)
    keys = {finding["key"] for finding in findings}
    assert "discovery.basketball_next_day_hrv" in keys
    finding = next(f for f in findings if f["key"] == "discovery.basketball_next_day_hrv")
    assert finding["evidence"]["n"] == 10
    assert finding["evidence"]["relative_effect"] < -0.05


def test_no_finding_without_consistent_effect(db):
    """Alternating HRV response ⇒ gate stays closed."""
    for index in range(10):
        day = f"2026-04-{2 * index + 1:02d}"
        after = f"2026-04-{2 * index + 2:02d}"
        _seed_hrv(db, day, 60.0)
        _seed_hrv(db, after, 66.0 if index % 2 == 0 else 54.0)
        db.upsert_activity({"id": f"b{index}", "date": day, "type": "basketball",
                            "duration_min": 90, "training_load": 150})
    keys = {finding["key"] for finding in discover_patterns(db)}
    assert "discovery.basketball_next_day_hrv" not in keys
