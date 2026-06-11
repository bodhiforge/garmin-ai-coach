"""Correlation discovery — personal pattern mining with statistical gates.

Python computes; findings below the gate never leave this module. Permutation
tests use a fixed seed: results are deterministic for a given dataset."""
from __future__ import annotations

import random
from typing import Any

from ..db.models import Database

DISCOVERY_MIN_PAIRS = 8
DISCOVERY_P_THRESHOLD = 0.05
DISCOVERY_MIN_RELATIVE_EFFECT = 0.05  # ≥5% shift vs baseline to matter
PERMUTATION_ITERATIONS = 2000
PERMUTATION_SEED = 7


def _sign_flip_p(deltas: list[float]) -> float:
    """Paired permutation test: under H0 each delta's sign is a coin flip."""
    rng = random.Random(PERMUTATION_SEED)
    observed = abs(sum(deltas) / len(deltas))
    hits = 0
    for _ in range(PERMUTATION_ITERATIONS):
        flipped_mean = sum(d if rng.random() < 0.5 else -d for d in deltas) / len(deltas)
        if abs(flipped_mean) >= observed:
            hits += 1
    return (hits + 1) / (PERMUTATION_ITERATIONS + 1)


def _label_shuffle_p(group_a: list[float], group_b: list[float]) -> float:
    """Two-sample permutation test on the difference of means."""
    rng = random.Random(PERMUTATION_SEED)
    pooled = group_a + group_b
    size_a = len(group_a)
    observed = abs(sum(group_a) / size_a - sum(group_b) / len(group_b))
    hits = 0
    for _ in range(PERMUTATION_ITERATIONS):
        shuffled = pooled[:]
        rng.shuffle(shuffled)
        mean_a = sum(shuffled[:size_a]) / size_a
        mean_b = sum(shuffled[size_a:]) / (len(pooled) - size_a)
        if abs(mean_a - mean_b) >= observed:
            hits += 1
    return (hits + 1) / (PERMUTATION_ITERATIONS + 1)


def gated_paired_effect(deltas: list[float], baseline_mean: float) -> dict[str, Any] | None:
    """Mean paired delta with permutation gate. None unless n, effect size,
    and significance all pass."""
    if len(deltas) < DISCOVERY_MIN_PAIRS or baseline_mean == 0:
        return None
    mean_delta = sum(deltas) / len(deltas)
    relative = mean_delta / abs(baseline_mean)
    if abs(relative) < DISCOVERY_MIN_RELATIVE_EFFECT:
        return None
    p_value = _sign_flip_p(deltas)
    if p_value >= DISCOVERY_P_THRESHOLD:
        return None
    return {
        "n": len(deltas),
        "mean_delta": round(mean_delta, 2),
        "relative_effect": round(relative, 3),
        "p": round(p_value, 4),
    }


def gated_two_sample_effect(
    group_a: list[float], group_b: list[float]
) -> dict[str, Any] | None:
    """Difference of means with permutation gate. group_a is the condition,
    group_b the comparison."""
    if len(group_a) < DISCOVERY_MIN_PAIRS or len(group_b) < DISCOVERY_MIN_PAIRS:
        return None
    mean_a = sum(group_a) / len(group_a)
    mean_b = sum(group_b) / len(group_b)
    if mean_b == 0:
        return None
    relative = (mean_a - mean_b) / abs(mean_b)
    if abs(relative) < DISCOVERY_MIN_RELATIVE_EFFECT:
        return None
    p_value = _label_shuffle_p(group_a, group_b)
    if p_value >= DISCOVERY_P_THRESHOLD:
        return None
    return {
        "n_condition": len(group_a),
        "n_comparison": len(group_b),
        "delta": round(mean_a - mean_b, 2),
        "relative_effect": round(relative, 3),
        "p": round(p_value, 4),
    }
