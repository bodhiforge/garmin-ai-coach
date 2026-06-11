"""Shared fixtures: a real Database against a temp file, plus seed helpers."""
from __future__ import annotations

from typing import Any

import pytest

from src.db.models import Database


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


def seed_strength_activity(
    db: Database, activity_id: str, day: str, sets: list[dict[str, Any]]
) -> None:
    """Insert a strength activity with gym sets."""
    db.upsert_activity({
        "id": activity_id,
        "date": day,
        "type": "strength",
        "duration_min": 60,
        "training_load": 80,
    })
    db.insert_gym_sets(activity_id, sets)


def make_set(exercise: str, reps: int, weight_lb: float | None, rest_sec: int = 90) -> dict[str, Any]:
    return {
        "set_number": 1,
        "exercise": exercise,
        "reps": reps,
        "weight_lb": weight_lb,
        "weight_kg": round(weight_lb / 2.2046, 2) if weight_lb is not None else None,
        "rest_duration_sec": rest_sec,
    }
