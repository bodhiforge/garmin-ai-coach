from src.ai.basketball_profile import hr_drift_pct, zone45_share


def test_hr_drift_detects_second_half_rise():
    # 40 minutes: first half avg ~150, second half avg ~165 ⇒ +10%.
    series = [(t * 60.0, 150) for t in range(20)] + [(1200 + t * 60.0, 165) for t in range(20)]
    drift = hr_drift_pct(series)
    assert drift is not None
    assert 9.0 <= drift <= 11.0


def test_hr_drift_requires_minimum_duration():
    series = [(t * 60.0, 150) for t in range(10)]  # 10 minutes only
    assert hr_drift_pct(series) is None


def test_zone45_share(db):
    db.upsert_activity({
        "id": "bb1", "date": "2026-06-01", "type": "basketball", "duration_min": 60,
        "hr_zone1_sec": 600, "hr_zone2_sec": 900, "hr_zone3_sec": 900,
        "hr_zone4_sec": 900, "hr_zone5_sec": 300,
    })
    shares = zone45_share(db, days=90)
    assert shares == [{"date": "2026-06-01", "share": 0.33}]
