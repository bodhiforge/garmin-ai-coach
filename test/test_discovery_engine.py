from src.ai.discovery import gated_paired_effect, gated_two_sample_effect


def test_paired_effect_fires_on_planted_signal():
    # 10 paired deltas, consistently negative ~-8 ⇒ significant.
    deltas = [-7.5, -8.2, -9.1, -6.8, -8.0, -7.9, -8.5, -9.3, -7.1, -8.8]
    result = gated_paired_effect(deltas, baseline_mean=60.0)
    assert result is not None
    assert result["n"] == 10
    assert result["p"] < 0.05
    assert result["relative_effect"] < -0.05


def test_paired_effect_silent_on_noise():
    deltas = [3.0, -2.5, 1.5, -3.5, 2.0, -1.0, 0.5, -0.5, 2.5, -2.0]
    assert gated_paired_effect(deltas, baseline_mean=60.0) is None


def test_paired_effect_silent_below_min_pairs():
    deltas = [-8.0] * 5
    assert gated_paired_effect(deltas, baseline_mean=60.0) is None


def test_two_sample_effect_fires_on_separated_groups():
    group_a = [52.0, 54.0, 50.0, 53.0, 51.0, 55.0, 52.5, 53.5]
    group_b = [60.0, 62.0, 61.0, 59.0, 63.0, 60.5, 61.5, 58.5]
    result = gated_two_sample_effect(group_a, group_b)
    assert result is not None
    assert result["p"] < 0.05
    assert result["delta"] < 0
