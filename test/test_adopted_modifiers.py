from src.ai.adopted import recovery_modifiers


def test_adopted_hrv_insight_becomes_recovery_multiplier(db):
    db.insert_insight(
        key="discovery.basketball_next_day_hrv",
        category="discovery",
        statement="HRV drops 22% after basketball",
        evidence={"n": 10, "relative_effect": -0.22, "mean_delta": -13.0, "p": 0.01},
    )
    row = db.get_insights()[0]
    db.mark_insight_adopted(row["id"], rule_ref="recovery_modifier")

    modifiers = recovery_modifiers(db)

    assert modifiers["basketball"] > 1.0          # recovers worse ⇒ longer recovery
    assert modifiers["basketball"] <= 1.5         # capped


def test_non_adopted_insights_have_no_effect(db):
    db.insert_insight(
        key="discovery.skiing_next_day_hrv",
        category="discovery",
        statement="s", evidence={"relative_effect": -0.10},
    )
    assert "skiing" not in recovery_modifiers(db)
