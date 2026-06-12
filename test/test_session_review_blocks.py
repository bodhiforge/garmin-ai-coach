from test.conftest import make_set, seed_strength_activity

from src.ai.session_review import review_block


def test_strength_block_includes_sets_and_pr(db):
    # history: RDL best e1RM from 45lb x10; new session: 50lb x10 ⇒ PR
    seed_strength_activity(db, "old", "2026-05-20", [make_set("Romanian Deadlift", 10, 45.0)])
    seed_strength_activity(db, "new", "2026-06-10", [
        make_set("Romanian Deadlift", 10, 50.0),
        make_set("Lat Pulldown", 12, 70.0),
    ])
    activity = next(a for a in db.get_recent_activities(days=90, activity_type="strength")
                    if a["id"] == "new")
    block = review_block(db, activity)
    assert "Romanian Deadlift" in block
    assert "PR" in block
    assert "ASK_FEEDBACK: yes" in block      # no RPE recorded for this session


def test_feedback_present_suppresses_question(db):
    seed_strength_activity(db, "s1", "2026-06-10", [make_set("Lat Pulldown", 12, 70.0)])
    db.insert_training_feedback("s1", rpe=7, notes="solid")
    activity = db.get_recent_activities(days=90, activity_type="strength")[0]
    block = review_block(db, activity)
    assert "ASK_FEEDBACK: no" in block
