from src.main import _write_deload_directive


def test_directive_written_with_cooldown(db, tmp_path):
    evidence = {"weekly_loads": [900.0, 750.0, 600.0, 450.0],
                "hrv_recent": 54.0, "hrv_baseline": 60.0,
                "readiness_recent": 62.0, "readiness_baseline": 71.0}
    target = tmp_path / "deload-directive.txt"

    assert _write_deload_directive(db, evidence, target) is True
    content = target.read_text()
    assert "40-50%" in content and "900" in content
    assert len(db.get_insights()) == 1               # audit-trail row
    # Cooldown: second fire inside 28 days is suppressed.
    assert _write_deload_directive(db, evidence, target) is False
