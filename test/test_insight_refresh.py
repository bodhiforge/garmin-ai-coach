import json


def test_upsert_refreshes_evidence_without_resetting_status(db):
    db.insert_insight(key="discovery.k", category="discovery",
                      statement="old statement", evidence={"n": 8, "relative_effect": -0.10})
    row = db.get_insights()[0]
    db.mark_insight_surfaced(row["id"])

    changed = db.refresh_insight_evidence(
        key="discovery.k",
        statement="new statement",
        evidence={"n": 14, "relative_effect": -0.22},
    )

    assert changed is True
    refreshed = db.get_insights()[0]
    assert refreshed["status"] == "surfaced"          # status preserved
    assert refreshed["statement"] == "new statement"
    assert json.loads(refreshed["evidence_json"])["n"] == 14


def test_refresh_returns_false_for_unknown_key(db):
    assert db.refresh_insight_evidence("nope", "s", {"n": 1}) is False
