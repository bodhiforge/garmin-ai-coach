from src.main import _write_health_alert


def test_alert_file_and_cooldown(db, tmp_path):
    warning = {
        "date": "2026-06-11",
        "fired_signals": ["respiration_avg", "resting_hr"],
        "details": {
            "respiration_avg": {"value": 17.5, "baseline": 14.0, "z": 2.4},
            "resting_hr": {"value": 58.0, "baseline": 52.0, "z": 2.1},
            "hrv_last_night": {"value": 59.0, "baseline": 60.0, "z": -0.3},
            "sleep_score": {"value": 78.0, "baseline": 80.0, "z": -0.4},
        },
    }
    alert_path = tmp_path / "health-alert.txt"

    wrote = _write_health_alert(db, warning, alert_path)

    assert wrote is True
    content = alert_path.read_text()
    assert "respiration_avg" in content and "17.5" in content
    # Cooldown recorded ⇒ second warning inside 48h is suppressed.
    assert _write_health_alert(db, warning, alert_path) is False
