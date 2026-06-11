from src.ai.strength_profile import strength_profile_block, strength_structural_findings

from test.conftest import make_set, seed_strength_activity


def _seed_imbalanced_history(db, sessions=12):
    """12 sessions, 9 pull + 1 push set each ⇒ 108 pull vs 12 push, no squat work."""
    for index in range(sessions):
        sets = [make_set("Lat Pulldown", 12, 70.0)] * 9 + [make_set("Cable Crossover", 12, 23.0)]
        month = 4 + index // 9
        day = index % 9 + 1
        seed_strength_activity(db, f"s{index}", f"2026-{month:02d}-{day:02d}", sets)


def test_findings_fire_with_sufficient_evidence(db):
    _seed_imbalanced_history(db)
    findings = {finding["key"] for finding in strength_structural_findings(db)}
    assert "strength.pull_push_imbalance" in findings
    assert "strength.no_squat_pattern" in findings
    assert "strength.no_strength_zone_work" in findings


def test_findings_stay_silent_on_thin_data(db):
    _seed_imbalanced_history(db, sessions=2)
    assert strength_structural_findings(db) == []


def test_profile_block_renders(db):
    _seed_imbalanced_history(db)
    block = strength_profile_block(db)
    assert block.startswith("## Strength Profile (computed — LLM MUST use this)")
    assert "Rep zones" in block
