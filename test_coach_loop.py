import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from src.ai.insights import (
    decision_logic,
    exercise_progression_layer,
    post_session_feedback_loop,
    professional_coach_layer,
    weekly_gap_analysis,
    weekly_programming_layer,
)
from src.db.models import Database


def _with_db():
    temp_dir = TemporaryDirectory()
    db = Database(Path(temp_dir.name) / "test.db")
    return temp_dir, db


def test_manual_sets_merge_into_feedback_loop() -> None:
    temp_dir, db = _with_db()
    try:
        db.upsert_activity({
            "id": "strength-1",
            "date": date.today().isoformat(),
            "type": "strength",
            "duration_min": 45,
            "training_load": 55,
        })
        db.insert_gym_sets(
            "strength-1",
            [{"set_number": 1, "exercise": "Lat Pulldown", "reps": 12, "weight_lb": None}],
        )
        db.insert_manual_gym_sets(
            "strength-1",
            [{"exercise": "Romanian Deadlift", "sets": 2, "reps": 10, "weight_lb": 20}],
            note="manual supplement",
        )

        loop = post_session_feedback_loop(db)

        assert "Weight capture: 2/3" in loop
        assert "posterior_chain" in loop
    finally:
        temp_dir.cleanup()


def test_pain_feedback_blocks_progression() -> None:
    temp_dir, db = _with_db()
    try:
        db.upsert_activity({
            "id": "strength-1",
            "date": "2026-05-13",
            "type": "strength",
            "duration_min": 45,
            "training_load": 55,
        })
        db.insert_manual_gym_sets(
            "strength-1",
            [{"exercise": "Romanian Deadlift", "sets": 3, "reps": 8, "weight_lb": 20}],
        )
        db.insert_training_feedback(
            "strength-1",
            rpe=7,
            pain_area="low back",
            pain_level=5,
            notes="felt sharp on hinge",
        )

        loop = post_session_feedback_loop(db)

        assert "Pain feedback overrides progression" in loop
        assert "low back 5/10" in loop
    finally:
        temp_dir.cleanup()


def test_programming_layers_exist_with_minimal_data() -> None:
    temp_dir, db = _with_db()
    try:
        db.upsert_activity({
            "id": "strength-1",
            "date": date.today().isoformat(),
            "type": "strength",
            "duration_min": 45,
            "training_load": 55,
        })
        db.insert_manual_gym_sets(
            "strength-1",
            [{"exercise": "Lat Pulldown", "sets": 2, "reps": 12, "weight_lb": 25}],
        )

        weekly = weekly_programming_layer(db)
        progression = exercise_progression_layer(db)

        assert "Weekly Programming Layer" in weekly
        assert "strength 1/2" in weekly
        assert "Exercise Progression Layer" in progression
        assert "Lat Pulldown" in progression
    finally:
        temp_dir.cleanup()


def test_basketball_plan_blocks_rescue_lifting() -> None:
    temp_dir, db = _with_db()
    previous_plan_path = os.environ.get("GARMIN_WEEKLY_PLAN_PATH")
    try:
        plan_path = Path(temp_dir.name) / "weekly-plan.md"
        plan_path.write_text("""---
week_starting: 2026-05-11
week_ending: 2026-05-17
sessions:
  - date: 2026-05-15
    day: Friday
    type: basketball
    status: planned
    prescription: |
      Basketball in the evening. Basketball only — no rescue lifting.
---

# Week
""")
        os.environ["GARMIN_WEEKLY_PLAN_PATH"] = str(plan_path)
        metrics = {
            "date": "2026-05-15",
            "hrv_last_night": 70,
            "hrv_weekly_avg": 69,
            "sleep_duration_min": 284,
            "body_battery_am": 47,
            "bb_at_wake": 76,
            "acwr_ratio": 0.60,
            "stress_avg": 18,
            "training_readiness_level": "MODERATE",
        }

        decision = decision_logic(db, metrics, target_date=date(2026, 5, 15))
        coach_layer = professional_coach_layer(db, metrics, target_date=date(2026, 5, 15))
        db.upsert_activity({
            "id": "strength-1",
            "date": "2026-05-14",
            "type": "strength",
            "duration_min": 85,
            "training_load": 40,
        })
        db.upsert_activity({
            "id": "strength-2",
            "date": "2026-05-12",
            "type": "strength",
            "duration_min": 89,
            "training_load": 102,
        })
        weekly_gap = weekly_gap_analysis(db, target_date=date(2026, 5, 15))

        assert "DECISION: SCHEDULED_BASKETBALL" in decision
        assert "basketball-first" in decision
        assert "separate gym session" in decision
        assert "Basketball-first constraint" in coach_layer
        assert "No rescue lifting" in coach_layer
        assert "home micro-session" in coach_layer
        assert "90/90 breathing" in coach_layer
        assert "easy walk" not in decision.lower()
        assert "easy walk" not in coach_layer.lower()
        assert "Fri 2026-05-15: morning swim" not in weekly_gap
        assert "light swim" in weekly_gap
    finally:
        if previous_plan_path is None:
            os.environ.pop("GARMIN_WEEKLY_PLAN_PATH", None)
        else:
            os.environ["GARMIN_WEEKLY_PLAN_PATH"] = previous_plan_path
        temp_dir.cleanup()


def test_actual_strength_overrides_planned_lower_overlap() -> None:
    temp_dir, db = _with_db()
    previous_plan_path = os.environ.get("GARMIN_WEEKLY_PLAN_PATH")
    try:
        plan_path = Path(temp_dir.name) / "weekly-plan.md"
        plan_path.write_text("""---
week_starting: 2026-05-18
week_ending: 2026-05-24
sessions:
  - date: 2026-05-20
    day: Wednesday
    type: strength_lower
    status: planned
    prescription: |
      Lower/posterior chain, 45-55 min.
      Weighted Hip Raise, Leg Curl, Light DB RDL, Pallof Press, Dead Bug.
---

# Week
""")
        os.environ["GARMIN_WEEKLY_PLAN_PATH"] = str(plan_path)
        db.upsert_activity({
            "id": "strength-actual",
            "date": "2026-05-19",
            "type": "strength",
            "duration_min": 60,
            "training_load": 48,
        })
        db.insert_manual_gym_sets("strength-actual", [
            {"exercise": "Romanian Deadlift", "sets": 3, "reps": 8, "weight_lb": 20},
            {"exercise": "Barbell Hip Thrust On Floor", "sets": 3, "reps": 12, "weight_lb": 90},
            {"exercise": "Weighted Leg Curl", "sets": 3, "reps": 16, "weight_lb": 40},
            {"exercise": "Lat Pulldown", "sets": 3, "reps": 12, "weight_lb": 55},
            {"exercise": "Seated Cable Row", "sets": 3, "reps": 12, "weight_lb": 55},
            {"exercise": "Face Pull", "sets": 3, "reps": 12, "weight_lb": 20},
            {"exercise": "Lateral Raise", "sets": 3, "reps": 12, "weight_lb": 5},
            {"exercise": "Cable Crossover", "sets": 3, "reps": 12, "weight_lb": 20},
        ])
        metrics = {
            "date": "2026-05-20",
            "hrv_last_night": 72,
            "hrv_weekly_avg": 66,
            "sleep_duration_min": 409,
            "body_battery_am": 57,
            "bb_at_wake": 91,
            "acwr_ratio": 1.40,
            "stress_avg": 19,
            "training_readiness_level": "MODERATE",
        }

        decision = decision_logic(db, metrics, target_date=date(2026, 5, 20))
        coach_layer = professional_coach_layer(db, metrics, target_date=date(2026, 5, 20))

        assert "DECISION: SINGLE_REST" in decision
        assert "adaptive recovery override" in decision
        assert "recovery/skill only" in decision
        assert "planned strength catch-up" in decision
        assert "Professional Sports Science Context" in coach_layer
        assert "Adaptive override" in coach_layer
        assert "actual completed training plus recovery/load gates override" in coach_layer
        assert "swim technique or easy Zone 1-2 cardio" in coach_layer
        assert "planned strength_lower overlaps" in coach_layer
        assert "Output a single adaptive recovery recommendation" not in decision
        assert "Programming dose:" not in coach_layer
        assert "Movement menu:" not in coach_layer
    finally:
        if previous_plan_path is None:
            os.environ.pop("GARMIN_WEEKLY_PLAN_PATH", None)
        else:
            os.environ["GARMIN_WEEKLY_PLAN_PATH"] = previous_plan_path
        temp_dir.cleanup()


if __name__ == "__main__":
    test_manual_sets_merge_into_feedback_loop()
    test_pain_feedback_blocks_progression()
    test_programming_layers_exist_with_minimal_data()
    test_basketball_plan_blocks_rescue_lifting()
    test_actual_strength_overrides_planned_lower_overlap()
    print("coach_loop_tests_ok")
