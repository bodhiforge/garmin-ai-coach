from datetime import datetime, timedelta

from src.ai.session_review import pending_reviews


def _now():
    return datetime.now()


def _seed_activity(db, activity_id, activity_type, hours_ago, duration_min=60):
    start = _now() - timedelta(hours=hours_ago, minutes=duration_min)
    db.upsert_activity({
        "id": activity_id,
        "date": str(start.date()),
        "type": activity_type,
        "duration_min": duration_min,
        "training_load": 100,
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
    })


def test_basketball_due_immediately_strength_waits(db):
    _seed_activity(db, "bb", "basketball", hours_ago=0.5)
    _seed_activity(db, "st", "strength", hours_ago=0.5)
    due_ids = {a["id"] for a in pending_reviews(db, now=_now())}
    assert "bb" in due_ids          # no buffer for non-strength
    assert "st" not in due_ids      # 2h buffer still running


def test_strength_due_after_buffer(db):
    _seed_activity(db, "st", "strength", hours_ago=2.5)
    assert {a["id"] for a in pending_reviews(db, now=_now())} == {"st"}


def test_walking_and_short_sessions_never_due(db):
    _seed_activity(db, "walk", "walking", hours_ago=5)
    _seed_activity(db, "tiny", "basketball", hours_ago=5, duration_min=10)
    assert pending_reviews(db, now=_now()) == []


def test_reviewed_activity_not_pending_again(db):
    _seed_activity(db, "bb", "basketball", hours_ago=1)
    db.add_notification("session_review_bb", "sent")
    assert pending_reviews(db, now=_now()) == []
