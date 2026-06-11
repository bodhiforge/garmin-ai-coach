from src.main import _write_weekly_insight_card


def test_card_surfaces_oldest_validated_insight(db, tmp_path):
    db.insert_insight(key="k1", category="strength", statement="oldest finding", evidence=None)
    db.insert_insight(key="k2", category="strength", statement="newer finding", evidence=None)
    card_path = tmp_path / "insight-card.txt"

    wrote = _write_weekly_insight_card(db, card_path)

    assert wrote is True
    assert "oldest finding" in card_path.read_text()
    assert len(db.get_insights(status="surfaced")) == 1
    # Second call the same day: next insight is NOT consumed (1/week throttle).
    assert _write_weekly_insight_card(db, card_path) is False


def test_card_noop_when_nothing_validated(db, tmp_path):
    card_path = tmp_path / "insight-card.txt"
    assert _write_weekly_insight_card(db, card_path) is False
    assert not card_path.exists()
