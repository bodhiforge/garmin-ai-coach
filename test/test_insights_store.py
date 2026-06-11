def test_insert_insight_dedups_by_key(db):
    first = db.insert_insight(
        key="strength.pull_push_imbalance",
        category="strength",
        statement="Pull volume is 4x push volume (99 vs 25 sets, 90d).",
        evidence={"pull_sets": 99, "push_sets": 25, "window_days": 90},
    )
    second = db.insert_insight(
        key="strength.pull_push_imbalance",
        category="strength",
        statement="duplicate",
        evidence=None,
    )
    assert first is True
    assert second is False
    rows = db.get_insights()
    assert len(rows) == 1
    assert rows[0]["status"] == "validated"


def test_insight_status_transitions(db):
    db.insert_insight(key="k1", category="observation", statement="s1", evidence=None)
    row = db.get_insights(status="validated")[0]
    db.mark_insight_surfaced(row["id"])
    surfaced = db.get_insights(status="surfaced")
    assert len(surfaced) == 1
    assert surfaced[0]["surfaced_date"] is not None
    assert db.get_insights(status="validated") == []
