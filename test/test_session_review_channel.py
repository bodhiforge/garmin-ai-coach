from datetime import datetime, timedelta

from src.main import _write_session_reviews


def test_writes_blocks_and_marks_reviewed(db, tmp_path):
    start = datetime.now() - timedelta(hours=3)
    db.upsert_activity({
        "id": "bb1", "date": str(start.date()), "type": "basketball",
        "duration_min": 60, "training_load": 150,
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
    })
    target = tmp_path / "session-review.txt"

    wrote = _write_session_reviews(db, target)

    assert wrote is True
    assert "basketball" in target.read_text()
    # marked reviewed ⇒ second call writes nothing
    assert _write_session_reviews(db, target) is False
