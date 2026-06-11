# Insight Foundation + Strength Intelligence Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the insights store, revive the orphaned observation detectors, and ship the Strength Intelligence module with a weekly "Did you know" surfacing channel.

**Architecture:** Pure-Python calculators run inside the existing `cmd_sync` */30 cron; validated findings persist to a new `insights` DB table (keyed dedup, status state machine); a Saturday card file surfaces at most one new insight per week to the Riko Deep Review. The LLM never computes — it only presents pre-computed, gated findings. Spec: `docs/superpowers/specs/2026-06-10-insight-discovery-pipeline-design.md`.

**Tech Stack:** Python 3 (stdlib only — sqlite3, statistics), pytest, existing `Database` class (`src/db/models.py`), deployment on mini via `git pull`.

**Phase split:** This plan is Phase 1 (spec §4.1, §4.4, §5, §6-weekly, §8, §9). Phase 2 (spec §4.2 discovery miner, §4.3 illness composite + instant push, §4.5 basketball, §6-monthly, §7 feedback loop, §2 contract prompt wiring on Riko) gets its own plan — written immediately when Task 12 completes (the trigger is Task 12's final step).

**Production caution:** mini is live. No code edits on mini's checkout — develop on macp, deploy by `git pull` on mini (Task 12 only).

---

### Task 0: Reconcile repo three-way divergence

mini is 20 commits ahead of origin **plus ~1553 uncommitted lines across 14 files** (the live coach layers). macp local has 1 divergent commit (`b68346e` "Add activity-aware training suggestions", likely superseded by mini's evolved `weekly_gap_analysis`) plus the spec commit (`eff30fc`). Origin is behind everything. Mini's lineage is production truth.

**Files:** none created — git state only.

- [ ] **Step 1: Commit mini's working tree as a WIP landing commit**

```bash
ssh mini 'cd ~/projects/garmin-ai-coach && git add -A && git commit -m "Land accumulated production work

Snapshot the live working tree: professional coach layer, weekly programming and exercise progression layers, post-session feedback loop, agent tool expansion, Garmin client and schema updates. Committed as a single landing commit so the insight pipeline work can build on the deployed lineage." && git log --oneline -3'
```

Expected: new commit on top of `8c5c605`, `git status` clean afterwards.

- [ ] **Step 2: Push mini to origin**

```bash
ssh mini 'cd ~/projects/garmin-ai-coach && git push origin main && git status --short --branch'
```

Expected: `## main...origin/main` with no ahead/behind marker.

- [ ] **Step 3: On macp, back up and inspect the divergent local commit**

```bash
cd ~/projects/garmin-ai-coach && git fetch origin && git branch backup/pre-reconcile-2026-06-11 && git show --stat b68346e
```

- [ ] **Step 4: Decide whether `b68346e` is superseded**

```bash
cd ~/projects/garmin-ai-coach && git grep -n "Outdoor/adventure" origin/main -- src/ai/insights.py | head -3 && git show b68346e -- src/ai/insights.py | head -40
```

Decision rule: if origin/main's `insights.py` already contains the activity-menu guidance (`Outdoor/adventure` lines in `weekly_gap_analysis`) covering what `b68346e` adds → drop it. Otherwise keep it in the rebase. Expected (per audit): superseded → drop.

- [ ] **Step 5: Rebase the spec commit onto origin/main, dropping the superseded commit**

```bash
cd ~/projects/garmin-ai-coach && git rebase --onto origin/main b68346e main && git log --oneline -4
```

Expected: history shows spec commit directly on top of mini's pushed lineage. (If Step 4 decided "keep", run `git rebase origin/main main` instead.)

- [ ] **Step 6: Push and verify all three states converged**

```bash
cd ~/projects/garmin-ai-coach && git push origin main && git status --short --branch && ssh mini 'cd ~/projects/garmin-ai-coach && git fetch origin && git status --short --branch'
```

Expected: macp clean and even with origin; mini shows `behind 1` (the spec commit — it gets pulled at deploy, Task 12).

---

### Task 1: pytest baseline

The repo has `pytest` as a dev dependency but no test directory.

**Files:**
- Create: `test/__init__.py` (empty), `test/conftest.py`, `test/test_db_smoke.py`
- Modify: `pyproject.toml` (add pytest config)

- [ ] **Step 1: Ensure a local venv with dev deps**

```bash
cd ~/projects/garmin-ai-coach && { [ -d .venv ] || python3 -m venv .venv; } && source .venv/bin/activate && pip install -e ".[dev]" -q && python -m pytest --version
```

- [ ] **Step 2: Add pytest config to `pyproject.toml`**

Append:

```toml
[tool.pytest.ini_options]
testpaths = ["test"]
```

- [ ] **Step 3: Write conftest with a fixture DB and seed helpers**

`test/conftest.py`:

```python
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
    """Insert a strength activity with gym sets. Column names follow the
    activities schema at src/db/models.py:35 — adjust only if upsert_activity
    rejects a key."""
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
```

- [ ] **Step 4: Write a roundtrip smoke test**

`test/test_db_smoke.py`:

```python
from test.conftest import make_set, seed_strength_activity


def test_activity_roundtrip(db):
    seed_strength_activity(db, "a1", "2026-06-01", [make_set("Lat Pulldown", 12, 70.0)])
    activities = db.get_recent_activities(days=3650, activity_type="strength")
    assert len(activities) == 1
    sets = db.get_gym_sets("a1")
    assert sets[0]["exercise"] == "Lat Pulldown"
    assert sets[0]["weight_lb"] == 70.0
```

- [ ] **Step 5: Run and fix seed-helper field names until green**

```bash
cd ~/projects/garmin-ai-coach && source .venv/bin/activate && python -m pytest -v
```

Expected: PASS. If `upsert_activity`/`insert_gym_sets` reject or drop a field, read their definitions (`src/db/models.py:317`, `:380`) and align the helper dicts — change the helpers, not the production code.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml test/ && git commit -m "Add pytest baseline with database fixtures"
```

---

### Task 2: Insights store — schema migration v6

**Files:**
- Modify: `src/db/models.py` (new migration + 4 methods)
- Test: `test/test_insights_store.py`

- [ ] **Step 1: Write failing tests**

`test/test_insights_store.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest test/test_insights_store.py -v
```

Expected: FAIL with `AttributeError: ... 'insert_insight'`.

- [ ] **Step 3: Add migration v6**

In `src/db/models.py`, after `_migrate_v5` (line ~230), following the existing static-method pattern:

```python
@staticmethod
def _migrate_v6(conn: sqlite3.Connection) -> None:
    """Insights store: discovered patterns with evidence and a status lifecycle."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            discovered_date TEXT NOT NULL,
            category TEXT NOT NULL,
            statement TEXT NOT NULL,
            evidence_json TEXT,
            status TEXT NOT NULL DEFAULT 'validated',
            surfaced_date TEXT,
            adopted_rule_ref TEXT
        )
        """
    )
```

Register it in `_init_schema`'s migration dispatch (read `src/db/models.py:148-176` and add version 6 exactly the way 5 is wired).

- [ ] **Step 4: Add the four access methods**

Next to the notification methods (after line ~590), matching local style:

```python
def insert_insight(
    self,
    key: str,
    category: str,
    statement: str,
    evidence: dict[str, Any] | None,
    status: str = "validated",
) -> bool:
    """Insert if key is new; returns True when a row was created."""
    with self._connection() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO insights"
            " (key, discovered_date, category, statement, evidence_json, status)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                key,
                str(date.today()),
                category,
                statement,
                json.dumps(evidence) if evidence is not None else None,
                status,
            ),
        )
        return cursor.rowcount > 0


def get_insights(self, status: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM insights"
    parameters: tuple[Any, ...] = ()
    if status is not None:
        query += " WHERE status = ?"
        parameters = (status,)
    query += " ORDER BY discovered_date ASC, id ASC"
    with self._connection() as conn:
        return [dict(row) for row in conn.execute(query, parameters).fetchall()]


def mark_insight_surfaced(self, insight_id: int) -> None:
    with self._connection() as conn:
        conn.execute(
            "UPDATE insights SET status = 'surfaced', surfaced_date = ? WHERE id = ?",
            (str(date.today()), insight_id),
        )


def mark_insight_adopted(self, insight_id: int, rule_ref: str) -> None:
    with self._connection() as conn:
        conn.execute(
            "UPDATE insights SET status = 'adopted', adopted_rule_ref = ? WHERE id = ?",
            (rule_ref, insight_id),
        )
```

Check imports: `json` and `date` are likely already imported in models.py; add if missing.

- [ ] **Step 5: Run tests**

```bash
python -m pytest test/test_insights_store.py -v
```

Expected: 2 PASS. Also run the full suite (`python -m pytest`) to confirm the migration doesn't break the smoke test.

- [ ] **Step 6: Commit**

```bash
git add src/db/models.py test/test_insights_store.py && git commit -m "Add insights store with keyed dedup and status lifecycle"
```

---

### Task 3: Revive observation detectors inside cmd_sync

`detect_observations` (`src/ai/observations.py`) is only called from `cmd_reflect`, whose cron was removed — orphaned since 2026-03-28. Wire it into the */30 sync path and persist to the insights store.

**Files:**
- Modify: `src/ai/observations.py` (persist to store), `src/main.py` (call from cmd_sync)
- Test: `test/test_observations_store.py`

- [ ] **Step 1: Write failing test**

`test/test_observations_store.py`:

```python
from pathlib import Path

from src.ai.observations import detect_observations

from test.conftest import make_set, seed_strength_activity


def _seed_low_readiness_training(db):
    """Two LOW-readiness days, trained on both — trips _rest_compliance."""
    for index, day in enumerate(["2026-06-01", "2026-06-02"]):
        db.upsert_daily_metrics({
            "date": day,
            "training_readiness_score": 20,
            "training_readiness_level": "LOW",
        })
        seed_strength_activity(db, f"a{index}", day, [make_set("Lat Pulldown", 12, 70.0)])


def test_observations_persist_to_insights_table(db, tmp_path):
    _seed_low_readiness_training(db)
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    found = detect_observations(db, memory_dir)
    if not found:
        # Seeding didn't trip any detector — fix the seed, not the assert.
        raise AssertionError("expected at least one observation from seeded data")
    stored = db.get_insights()
    assert len(stored) >= 1
    assert all(row["category"] == "observation" for row in stored)
    assert (memory_dir / "observations.md").exists()
```

Run: `python -m pytest test/test_observations_store.py -v` — expected FAIL (`get_insights` returns `[]`: detectors don't write to the store yet). If `detect_observations` itself returns nothing, read `_rest_compliance` in `src/ai/observations.py` and adjust the seeded metrics to its actual thresholds (readiness level/score fields), then re-run until the test fails on the *store* assertion specifically.

- [ ] **Step 2: Persist observations to the insights table**

In `src/ai/observations.py::detect_observations`, the function already computes `new_observations` with a dedup key (`obs_key`). After the block that appends to `observations.md`, add:

```python
    for obs in new_observations:
        obs_key = obs.split(":")[0].strip() if ":" in obs else obs[:40]
        db.insert_insight(
            key=f"observation.{obs_key}",
            category="observation",
            statement=obs,
            evidence=None,
        )
```

(The `.md` file stays as the human/LLM-readable mirror; the table is the source of truth for surfacing.)

- [ ] **Step 3: Run test to verify it passes**

```bash
python -m pytest test/test_observations_store.py -v
```

Expected: PASS.

- [ ] **Step 4: Call detectors from cmd_sync**

In `src/main.py::cmd_sync` (starts line ~406): locate the point after the sync calls (`sync.sync_daily_metrics()` / `sync.sync_activities()` / gym-set refresh) and **before** the wake-detection phase branching. Insert:

```python
    # Pattern detection runs every sync; keyed dedup makes it idempotent.
    from .ai.observations import detect_observations
    try:
        new_observations = detect_observations(sync.db, coach.memory_dir)
        for observation in new_observations:
            print(f"New observation: {observation}")
    except Exception as error:
        logger.warning("Observation detection failed: %s", error)
```

Detection must never break the sync state machine — hence the broad catch.

- [ ] **Step 5: Manual sanity run against a DB copy**

```bash
scp mini:projects/garmin-ai-coach/data/garmin.db /tmp/garmin-prod-copy.db && cd ~/projects/garmin-ai-coach && source .venv/bin/activate && python -c "
from pathlib import Path
from src.db.models import Database
from src.ai.observations import detect_observations
db = Database('/tmp/garmin-prod-copy.db')
print(detect_observations(db, Path('/tmp/obs-test')))
print([ (r['key'], r['statement']) for r in db.get_insights() ])
"
```

Expected: detectors run on 3 months of real data; new observations print (the copy's `observations.md` is empty so prior March findings may re-fire — fine, it's a copy).

- [ ] **Step 6: Commit**

```bash
git add src/ai/observations.py src/main.py test/test_observations_store.py && git commit -m "Revive observation detectors inside cmd_sync and persist them to the insights store"
```

---

### Task 4: strength_profile.py — data loading and exercise taxonomy

**Files:**
- Create: `src/ai/strength_profile.py`
- Test: `test/test_strength_loading.py`

- [ ] **Step 1: Write failing tests**

`test/test_strength_loading.py`:

```python
from src.ai.strength_profile import load_strength_sets

from test.conftest import make_set, seed_strength_activity


def test_excludes_non_lift_entries(db):
    seed_strength_activity(db, "a1", "2026-06-01", [
        make_set("Lat Pulldown", 12, 70.0),
        make_set("Treadmill", 195, None),
        make_set("Stretch Hip Flexor And Quad", 1, None),
    ])
    rows = load_strength_sets(db, days=30)
    assert [row["exercise"] for row in rows] == ["Lat Pulldown"]


def test_rows_carry_session_date(db):
    seed_strength_activity(db, "a1", "2026-06-01", [make_set("Romanian Deadlift", 10, 45.0)])
    rows = load_strength_sets(db, days=30)
    assert rows[0]["date"] == "2026-06-01"
    assert rows[0]["weight_lb"] == 45.0
```

Run: `python -m pytest test/test_strength_loading.py -v` — expected FAIL (module not found).

- [ ] **Step 2: Implement loader + taxonomy**

`src/ai/strength_profile.py`:

```python
"""Strength intelligence — pure Python calculators over gym set history.

Python computes every number. The LLM only presents gated findings.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import median
from typing import Any

from ..db.models import Database

EXCLUDED_EXERCISES = {"Treadmill"}
EXCLUDED_PREFIXES = ("Stretch",)

EXERCISE_MUSCLE_MAP: dict[str, tuple[str, ...]] = {
    "Romanian Deadlift": ("hamstrings", "glutes"),
    "Seated Cable Row": ("back",),
    "Lat Pulldown": ("back",),
    "Straight Arm Pulldown": ("back",),
    "Face Pull": ("rear_delts",),
    "Lateral Raise": ("side_delts",),
    "Cable Crossover": ("chest",),
    "Shoulder Press": ("front_delts",),
    "Barbell Hip Thrust On Floor": ("glutes",),
    "Weighted Hip Raise": ("glutes",),
    "Hip Raise": ("glutes",),
    "Weighted Standing Hip Abduction": ("glute_med",),
    "Weighted Sliding Hip Adduction": ("adductors",),
    "Weighted Leg Curl": ("hamstrings",),
    "Leg Curl": ("hamstrings",),
    "Dumbbell Bulgarian Split Squat": ("quads", "glutes"),
    "Overhead Bulgarian Split Squat": ("quads", "glutes"),
    "Leg Press": ("quads", "glutes"),
    "Cable Woodchop": ("core",),
    "Weighted Sit Up": ("core",),
    "Leg Raise": ("core",),
}

EXERCISE_PATTERN_MAP: dict[str, str] = {
    "Romanian Deadlift": "hinge",
    "Barbell Hip Thrust On Floor": "hinge",
    "Weighted Hip Raise": "hinge",
    "Hip Raise": "hinge",
    "Dumbbell Bulgarian Split Squat": "lunge",
    "Overhead Bulgarian Split Squat": "lunge",
    "Leg Press": "squat",
    "Seated Cable Row": "pull_h",
    "Face Pull": "pull_h",
    "Lat Pulldown": "pull_v",
    "Straight Arm Pulldown": "pull_v",
    "Cable Crossover": "push_h",
    "Shoulder Press": "push_v",
    "Cable Woodchop": "core",
    "Weighted Sit Up": "core",
    "Leg Raise": "core",
}

CORE_PATTERNS = ("squat", "hinge", "lunge", "push_h", "push_v", "pull_h", "pull_v", "core")

MAJOR_MUSCLE_GROUPS = ("back", "chest", "quads", "hamstrings", "glutes")
WEEKLY_SET_FLOOR = 10   # below ⇒ likely under-stimulating (MEV reference)
WEEKLY_SET_CEILING = 20  # above ⇒ likely junk volume / recovery debt (MAV reference)

STRENGTH_REP_MAX = 6
HYPERTROPHY_REP_MAX = 12


def _is_lift(exercise: str) -> bool:
    if exercise == "" or exercise in EXCLUDED_EXERCISES:
        return False
    return not exercise.startswith(EXCLUDED_PREFIXES)


def load_strength_sets(db: Database, days: int = 90) -> list[dict[str, Any]]:
    """Flat list of lift sets across recent strength sessions, newest first.
    Each row: exercise, reps, weight_lb, rest_duration_sec, date, activity_id."""
    activities = db.get_recent_activities(days=days, activity_type="strength")
    rows: list[dict[str, Any]] = []
    for activity in activities:
        for set_row in db.get_gym_sets(activity["id"]):
            exercise = str(set_row.get("exercise") or "").strip()
            if not _is_lift(exercise):
                continue
            rows.append({
                "exercise": exercise,
                "reps": set_row.get("reps"),
                "weight_lb": set_row.get("weight_lb"),
                "rest_duration_sec": set_row.get("rest_duration_sec"),
                "date": activity.get("date"),
                "activity_id": activity["id"],
            })
    return rows
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest test/test_strength_loading.py -v
```

Expected: 2 PASS. Note: if `get_gym_sets` does not already merge `manual_gym_sets` (check `src/db/models.py:402` — the `source` field suggests it does), extend `load_strength_sets` to also read manual sets for the same activities.

- [ ] **Step 4: Commit**

```bash
git add src/ai/strength_profile.py test/test_strength_loading.py && git commit -m "Add strength set loader with exercise taxonomy and non-lift filtering"
```

---

### Task 5: e1RM trend + plateau detection

**Files:**
- Modify: `src/ai/strength_profile.py`
- Test: `test/test_strength_e1rm.py`

- [ ] **Step 1: Write failing tests**

`test/test_strength_e1rm.py`:

```python
from src.ai.strength_profile import e1rm, e1rm_trend

from test.conftest import make_set, seed_strength_activity


def test_epley_formula():
    assert e1rm(100.0, 1) == 100.0
    assert round(e1rm(100.0, 10), 1) == 133.3


def _seed_sessions(db, weights):
    for index, weight in enumerate(weights):
        day = f"2026-05-{index + 1:02d}"
        seed_strength_activity(db, f"a{index}", day, [make_set("Romanian Deadlift", 10, weight)])


def test_plateau_detected_after_flat_sessions(db):
    _seed_sessions(db, [40.0, 45.0, 45.0, 45.0, 45.0])
    trend = e1rm_trend(db, days=90)["Romanian Deadlift"]
    assert trend["plateau"] is True
    assert trend["sessions"] == 5


def test_no_plateau_while_progressing(db):
    _seed_sessions(db, [40.0, 42.5, 45.0, 47.5])
    trend = e1rm_trend(db, days=90)["Romanian Deadlift"]
    assert trend["plateau"] is False
```

Run: `python -m pytest test/test_strength_e1rm.py -v` — expected FAIL (`e1rm` not defined).

- [ ] **Step 2: Implement**

Append to `src/ai/strength_profile.py`:

```python
PLATEAU_MIN_SESSIONS = 4
PLATEAU_BAND = 0.025  # last 3 session-bests within ±2.5% ⇒ flat


def e1rm(weight_lb: float, reps: int) -> float:
    """Epley estimated 1-rep max."""
    if reps <= 1:
        return weight_lb
    return weight_lb * (1 + reps / 30)


def e1rm_trend(db: Database, days: int = 90) -> dict[str, dict[str, Any]]:
    """Per exercise: session-best e1RM series (date ascending) + plateau flag."""
    session_best: dict[str, dict[str, float]] = defaultdict(dict)
    for row in load_strength_sets(db, days=days):
        if row["weight_lb"] is None or row["weight_lb"] <= 0 or not row["reps"]:
            continue
        estimate = e1rm(row["weight_lb"], row["reps"])
        day = str(row["date"])
        best = session_best[row["exercise"]]
        best[day] = max(best.get(day, 0.0), estimate)

    trend: dict[str, dict[str, Any]] = {}
    for exercise, by_day in session_best.items():
        series = [round(by_day[day], 1) for day in sorted(by_day)]
        recent = series[-3:]
        is_flat = (
            len(series) >= PLATEAU_MIN_SESSIONS
            and len(recent) == 3
            and (max(recent) - min(recent)) <= PLATEAU_BAND * max(recent)
            and max(recent) <= max(series[:-3] + [recent[0]])
        )
        trend[exercise] = {
            "series": series,
            "latest_e1rm": series[-1],
            "best_e1rm": max(series),
            "sessions": len(series),
            "plateau": is_flat,
        }
    return trend
```

- [ ] **Step 3: Run tests, full suite, commit**

```bash
python -m pytest test/test_strength_e1rm.py -v && python -m pytest -q
git add src/ai/strength_profile.py test/test_strength_e1rm.py && git commit -m "Add e1RM trend and plateau detection per exercise"
```

---

### Task 6: Muscle-group weekly volume + movement pattern matrix

**Files:**
- Modify: `src/ai/strength_profile.py`
- Test: `test/test_strength_volume.py`

- [ ] **Step 1: Write failing tests**

`test/test_strength_volume.py`:

```python
from src.ai.strength_profile import movement_pattern_matrix, weekly_muscle_volume

from test.conftest import make_set, seed_strength_activity


def _seed_pull_heavy_month(db):
    # 4 weekly sessions: 6 back pull sets each, 1 chest push set each.
    for week in range(4):
        sets = [make_set("Lat Pulldown", 12, 70.0)] * 3 + \
               [make_set("Seated Cable Row", 12, 55.0)] * 3 + \
               [make_set("Cable Crossover", 12, 23.0)]
        seed_strength_activity(db, f"w{week}", f"2026-05-{7 * week + 1:02d}", sets)


def test_weekly_volume_flags_low_groups(db):
    _seed_pull_heavy_month(db)
    volume = weekly_muscle_volume(db, days=28)
    assert volume["back"]["weekly_sets"] == 6.0
    assert volume["back"]["flag"] == "below_floor"   # 6 < 10
    assert volume["chest"]["weekly_sets"] == 1.0
    assert volume["quads"]["weekly_sets"] == 0.0


def test_pattern_matrix_reports_gaps(db):
    _seed_pull_heavy_month(db)
    matrix = movement_pattern_matrix(db, days=28)
    assert matrix["counts"]["pull_v"] == 12
    assert matrix["counts"]["squat"] == 0
    assert "squat" in matrix["gaps"]
    assert "hinge" in matrix["gaps"]
```

Run: `python -m pytest test/test_strength_volume.py -v` — expected FAIL.

- [ ] **Step 2: Implement**

Append to `src/ai/strength_profile.py`:

```python
def weekly_muscle_volume(db: Database, days: int = 28) -> dict[str, dict[str, Any]]:
    """Average weekly working sets per major muscle group vs volume landmarks."""
    weeks = max(days / 7, 1)
    set_counts: dict[str, int] = defaultdict(int)
    for row in load_strength_sets(db, days=days):
        for muscle in EXERCISE_MUSCLE_MAP.get(row["exercise"], ()):
            set_counts[muscle] += 1

    volume: dict[str, dict[str, Any]] = {}
    for muscle in MAJOR_MUSCLE_GROUPS:
        weekly_sets = round(set_counts.get(muscle, 0) / weeks, 1)
        flag = "ok"
        if weekly_sets < WEEKLY_SET_FLOOR:
            flag = "below_floor"
        elif weekly_sets > WEEKLY_SET_CEILING:
            flag = "above_ceiling"
        volume[muscle] = {"weekly_sets": weekly_sets, "flag": flag}
    return volume


def movement_pattern_matrix(db: Database, days: int = 90) -> dict[str, Any]:
    """Set counts per movement pattern + list of uncovered core patterns."""
    counts: dict[str, int] = {pattern: 0 for pattern in CORE_PATTERNS}
    unmapped: set[str] = set()
    for row in load_strength_sets(db, days=days):
        pattern = EXERCISE_PATTERN_MAP.get(row["exercise"])
        if pattern is None:
            unmapped.add(row["exercise"])
            continue
        if pattern in counts:
            counts[pattern] += 1
    gaps = [pattern for pattern in CORE_PATTERNS if counts[pattern] == 0]
    return {"counts": counts, "gaps": gaps, "unmapped": sorted(unmapped)}
```

Note: isolation moves (Face Pull → pull_h, Lateral Raise unmapped) intentionally don't all land in the matrix — `unmapped` keeps them visible without polluting pattern counts.

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest test/test_strength_volume.py -v && python -m pytest -q
git add src/ai/strength_profile.py test/test_strength_volume.py && git commit -m "Add weekly muscle volume landmarks and movement pattern matrix"
```

---

### Task 7: Rep-zone distribution + rest-interval analysis

**Files:**
- Modify: `src/ai/strength_profile.py`
- Test: `test/test_strength_zones.py`

- [ ] **Step 1: Write failing tests**

`test/test_strength_zones.py`:

```python
from src.ai.strength_profile import rep_zone_distribution, rest_interval_analysis

from test.conftest import make_set, seed_strength_activity


def test_rep_zones_all_hypertrophy(db):
    seed_strength_activity(db, "a1", "2026-06-01", [
        make_set("Romanian Deadlift", 10, 45.0),
        make_set("Lat Pulldown", 12, 70.0),
    ])
    zones = rep_zone_distribution(db, days=90)
    assert zones["strength_pct"] == 0.0
    assert zones["hypertrophy_pct"] == 100.0
    assert zones["total_sets"] == 2


def test_rest_flags_rushed_compounds(db):
    seed_strength_activity(db, "a1", "2026-06-01", [
        make_set("Romanian Deadlift", 10, 45.0, rest_sec=45),
        make_set("Romanian Deadlift", 10, 45.0, rest_sec=50),
        make_set("Lateral Raise", 12, 5.0, rest_sec=40),
    ])
    rest = rest_interval_analysis(db, days=90)
    assert rest["compound_median_sec"] == 47.5
    assert rest["rushed_compounds"] is True
```

Run: `python -m pytest test/test_strength_zones.py -v` — expected FAIL.

- [ ] **Step 2: Implement**

Append to `src/ai/strength_profile.py`:

```python
COMPOUND_PATTERNS = ("squat", "hinge", "lunge", "push_h", "push_v", "pull_h", "pull_v")
COMPOUND_REST_FLOOR_SEC = 60


def rep_zone_distribution(db: Database, days: int = 90) -> dict[str, Any]:
    """Share of working sets per rep zone: strength ≤6, hypertrophy 7-12, endurance 13+."""
    strength_sets = hypertrophy_sets = endurance_sets = 0
    for row in load_strength_sets(db, days=days):
        reps = row["reps"]
        if not reps or reps <= 0:
            continue
        if reps <= STRENGTH_REP_MAX:
            strength_sets += 1
        elif reps <= HYPERTROPHY_REP_MAX:
            hypertrophy_sets += 1
        else:
            endurance_sets += 1
    total = strength_sets + hypertrophy_sets + endurance_sets
    if total == 0:
        return {"total_sets": 0, "strength_pct": 0.0, "hypertrophy_pct": 0.0, "endurance_pct": 0.0}
    return {
        "total_sets": total,
        "strength_pct": round(100 * strength_sets / total, 1),
        "hypertrophy_pct": round(100 * hypertrophy_sets / total, 1),
        "endurance_pct": round(100 * endurance_sets / total, 1),
    }


def rest_interval_analysis(db: Database, days: int = 90) -> dict[str, Any]:
    """Median rest on compound patterns; flags chronically rushed rests."""
    compound_rests = [
        row["rest_duration_sec"]
        for row in load_strength_sets(db, days=days)
        if row["rest_duration_sec"] is not None
        and EXERCISE_PATTERN_MAP.get(row["exercise"]) in COMPOUND_PATTERNS
    ]
    if not compound_rests:
        return {"compound_median_sec": None, "rushed_compounds": False, "sample": 0}
    median_rest = median(compound_rests)
    return {
        "compound_median_sec": round(median_rest, 1),
        "rushed_compounds": median_rest < COMPOUND_REST_FLOOR_SEC,
        "sample": len(compound_rests),
    }
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest test/test_strength_zones.py -v && python -m pytest -q
git add src/ai/strength_profile.py test/test_strength_zones.py && git commit -m "Add rep-zone distribution and compound rest-interval analysis"
```

---

### Task 8: Formatted profile block + structural findings with statistical gates

**Files:**
- Modify: `src/ai/strength_profile.py`
- Test: `test/test_strength_findings.py`

- [ ] **Step 1: Write failing tests**

`test/test_strength_findings.py`:

```python
from src.ai.strength_profile import strength_profile_block, strength_structural_findings

from test.conftest import make_set, seed_strength_activity


def _seed_imbalanced_history(db, sessions=12):
    """12 sessions, 9 pull + 1 push set each ⇒ 108 pull vs 12 push, no squat work."""
    for index in range(sessions):
        sets = [make_set("Lat Pulldown", 12, 70.0)] * 9 + [make_set("Cable Crossover", 12, 23.0)]
        seed_strength_activity(db, f"s{index}", f"2026-{3 + index // 9:02d}-{index % 9 + 1:02d}", sets)


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
```

Run: `python -m pytest test/test_strength_findings.py -v` — expected FAIL.

- [ ] **Step 2: Implement**

Append to `src/ai/strength_profile.py`:

```python
FINDING_MIN_SESSIONS = 10
FINDING_MIN_SETS = 80
PULL_PUSH_RATIO_GATE = 2.0


def strength_structural_findings(db: Database, days: int = 90) -> list[dict[str, Any]]:
    """Gated structural findings ready for the insights store. Empty on thin data."""
    rows = load_strength_sets(db, days=days)
    sessions = len({row["activity_id"] for row in rows})
    if sessions < FINDING_MIN_SESSIONS or len(rows) < FINDING_MIN_SETS:
        return []

    findings: list[dict[str, Any]] = []
    matrix = movement_pattern_matrix(db, days=days)
    counts = matrix["counts"]

    pull_sets = counts["pull_h"] + counts["pull_v"]
    push_sets = counts["push_h"] + counts["push_v"]
    if push_sets > 0 and pull_sets / push_sets >= PULL_PUSH_RATIO_GATE:
        ratio = round(pull_sets / push_sets, 1)
        findings.append({
            "key": "strength.pull_push_imbalance",
            "statement": (
                f"Pull volume is {ratio}x push volume over the last {days} days"
                f" ({pull_sets} vs {push_sets} sets). Deliberate posture bias or drift?"
            ),
            "evidence": {"pull_sets": pull_sets, "push_sets": push_sets,
                         "ratio": ratio, "sessions": sessions, "window_days": days},
        })

    if counts["squat"] == 0:
        findings.append({
            "key": "strength.no_squat_pattern",
            "statement": (
                f"Zero squat-pattern sets across {sessions} sessions in {days} days —"
                " all knee-dominant work is lunge variants. Worth a deliberate decision."
            ),
            "evidence": {"sessions": sessions, "lunge_sets": counts["lunge"], "window_days": days},
        })

    zones = rep_zone_distribution(db, days=days)
    if zones["total_sets"] >= FINDING_MIN_SETS and zones["strength_pct"] == 0.0:
        findings.append({
            "key": "strength.no_strength_zone_work",
            "statement": (
                f"0% of {zones['total_sets']} sets in the ≤{STRENGTH_REP_MAX}-rep strength zone"
                f" ({days}d) — everything lives at {zones['hypertrophy_pct']}% hypertrophy /"
                f" {zones['endurance_pct']}% endurance reps."
            ),
            "evidence": zones | {"window_days": days},
        })

    rest = rest_interval_analysis(db, days=days)
    if rest["sample"] >= FINDING_MIN_SETS and rest["rushed_compounds"]:
        findings.append({
            "key": "strength.rushed_compound_rests",
            "statement": (
                f"Median rest on compound lifts is {rest['compound_median_sec']}s"
                f" (n={rest['sample']}) — under {COMPOUND_REST_FLOOR_SEC}s, which caps load progression."
            ),
            "evidence": rest | {"window_days": days},
        })

    return findings


def store_strength_findings(db: Database) -> int:
    """Persist gated findings; returns number of new rows."""
    inserted = 0
    for finding in strength_structural_findings(db):
        if db.insert_insight(
            key=finding["key"],
            category="strength",
            statement=finding["statement"],
            evidence=finding["evidence"],
        ):
            inserted += 1
    return inserted


def strength_profile_block(db: Database, days: int = 90) -> str:
    """Formatted profile for digests and on-demand queries — same register as
    the existing computed layers in insights.py."""
    lines = ["## Strength Profile (computed — LLM MUST use this)"]

    trend = e1rm_trend(db, days=days)
    for exercise in sorted(trend, key=lambda name: -trend[name]["sessions"])[:8]:
        info = trend[exercise]
        marker = " — PLATEAU" if info["plateau"] else ""
        lines.append(
            f"- {exercise}: e1RM {info['latest_e1rm']}lb (best {info['best_e1rm']}lb,"
            f" {info['sessions']} sessions){marker}"
        )

    volume = weekly_muscle_volume(db)
    volume_parts = [
        f"{muscle} {data['weekly_sets']}/wk"
        + (" LOW" if data["flag"] == "below_floor" else " HIGH" if data["flag"] == "above_ceiling" else "")
        for muscle, data in volume.items()
    ]
    lines.append("Weekly sets vs landmarks: " + ", ".join(volume_parts))

    matrix = movement_pattern_matrix(db, days=days)
    lines.append(
        "Pattern coverage: "
        + ", ".join(f"{pattern} {count}" for pattern, count in matrix["counts"].items())
        + (f" | gaps: {', '.join(matrix['gaps'])}" if matrix["gaps"] else "")
    )

    zones = rep_zone_distribution(db, days=days)
    lines.append(
        f"Rep zones ({zones['total_sets']} sets): strength {zones['strength_pct']}%,"
        f" hypertrophy {zones['hypertrophy_pct']}%, endurance {zones['endurance_pct']}%"
    )

    rest = rest_interval_analysis(db, days=days)
    if rest["compound_median_sec"] is not None:
        lines.append(
            f"Compound rest median: {rest['compound_median_sec']}s (n={rest['sample']})"
            + (" — RUSHED" if rest["rushed_compounds"] else "")
        )

    return "\n".join(lines)
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest test/test_strength_findings.py -v && python -m pytest -q
git add src/ai/strength_profile.py test/test_strength_findings.py && git commit -m "Add gated structural findings and formatted strength profile block"
```

---

### Task 9: CLI entry point for interactive strength Q&A

Riko answers "我的 RDL 怎么样了" by shelling out to this command — single computation, two outlets (spec §4.4).

**Files:**
- Modify: `src/main.py` (new subcommand)
- Test: manual CLI run (argparse glue; calculators already unit-tested)

- [ ] **Step 1: Add the command function**

In `src/main.py`, next to the other `cmd_*` functions:

```python
def cmd_strength_profile(args: argparse.Namespace) -> None:
    """Print the computed strength profile. Consumed by Riko for Q&A."""
    config, _, db, _, _, _ = build_components(args.config)
    from .ai.strength_profile import strength_profile_block, strength_structural_findings
    print(strength_profile_block(db, days=args.days))
    findings = strength_structural_findings(db, days=args.days)
    if findings:
        print("\n## Structural Findings")
        for finding in findings:
            print(f"- {finding['statement']}")
```

**Check `build_components`' actual return tuple first** (it appears at the top of other `cmd_*` functions, e.g. `cmd_reflect` at src/main.py:617) and unpack the database the same way they do — the tuple order above is a guess and the existing code is authoritative. If components only expose `sync.db`, use that.

- [ ] **Step 2: Register the subparser**

Find the argparse subparser block (search `add_parser(` in `src/main.py`) and register alongside the others:

```python
    strength_parser = subparsers.add_parser("strength-profile", help="Computed strength profile")
    strength_parser.add_argument("--days", type=int, default=90)
    strength_parser.set_defaults(func=cmd_strength_profile)
```

- [ ] **Step 3: Manual verify against the prod copy**

```bash
cd ~/projects/garmin-ai-coach && source .venv/bin/activate && python -m src.main strength-profile --days 90 --config config.example.yaml 2>/dev/null || true
```

If the config path makes this awkward locally, point the data dir at `/tmp/garmin-prod-copy.db` via a temp config. Expected output: profile block with real e1RM lines for RDL / Lat Pulldown / etc., pattern gaps including `squat`, rep zones ~0% strength.

- [ ] **Step 4: Commit**

```bash
git add src/main.py && git commit -m "Add strength-profile CLI command for Riko interactive queries"
```

---

### Task 10: Wire strength findings + Saturday insight card into cmd_sync

**Files:**
- Modify: `src/main.py`
- Test: `test/test_insight_card.py`

- [ ] **Step 1: Write failing test for card selection logic**

The card writer is pure logic + one file write; test it directly.

`test/test_insight_card.py`:

```python
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
```

Run: `python -m pytest test/test_insight_card.py -v` — expected FAIL (function missing).

- [ ] **Step 2: Implement the card writer in `src/main.py`**

```python
def _write_weekly_insight_card(db, card_path: Path | None = None) -> bool:
    """Surface at most one validated insight per week as the Deep Review card.
    Returns True when a card was written."""
    from datetime import date as date_type
    target = card_path or (Path.home() / "ai" / "data" / "signals" / "insight-card.txt")

    already_this_week = [
        row for row in db.get_insights(status="surfaced")
        if row["surfaced_date"]
        and (date_type.today() - date_type.fromisoformat(row["surfaced_date"])).days < 7
    ]
    if already_this_week:
        return False

    validated = db.get_insights(status="validated")
    if not validated:
        return False

    top = validated[0]  # oldest first — FIFO keeps the queue honest
    evidence = f"\nEvidence: {top['evidence_json']}" if top["evidence_json"] else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"# Did You Know — {date_type.today()}\n\n{top['statement']}{evidence}\n"
    )
    db.mark_insight_surfaced(top["id"])
    return True
```

- [ ] **Step 3: Run card tests**

```bash
python -m pytest test/test_insight_card.py -v
```

Expected: 2 PASS.

- [ ] **Step 4: Hook findings + card into cmd_sync**

In `cmd_sync`, directly after the Task 3 observation block, add:

```python
    # Strength findings are gated and key-deduped; cheap to run every sync.
    from .ai.strength_profile import store_strength_findings
    try:
        new_findings = store_strength_findings(sync.db)
        if new_findings:
            print(f"New strength findings stored: {new_findings}")
    except Exception as error:
        logger.warning("Strength finding detection failed: %s", error)

    # Saturday: surface at most one validated insight for the Deep Review.
    if date.today().weekday() == 5:
        try:
            if _write_weekly_insight_card(sync.db):
                print("Weekly insight card written")
        except Exception as error:
            logger.warning("Insight card failed: %s", error)
```

(`date` is already imported in main.py — `_write_training_digest` uses it.)

- [ ] **Step 5: Run full suite, commit**

```bash
python -m pytest -q
git add src/main.py test/test_insight_card.py && git commit -m "Wire strength findings and Saturday insight card into the sync state machine

cmd_sync now runs three cheap pure-Python passes after data sync: observation detectors, gated strength findings (both keyed-dedup into the insights store), and on Saturdays a card writer that surfaces at most one validated insight per week for the Riko Deep Review."
```

---

### Task 11: Backtest against production data (manual review gate)

Spec §9: every insight produced from real data gets human review before anything reaches Telegram.

**Files:**
- Create: `scripts/backtest_insights.py`

- [ ] **Step 1: Write the backtest script**

`scripts/backtest_insights.py`:

```python
"""Run all Phase-1 detectors against a copy of the production DB and print
everything they would store/surface. Read-only on production: always point
this at a copy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.observations import detect_observations
from src.ai.strength_profile import strength_profile_block, strength_structural_findings
from src.db.models import Database

db_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/garmin-prod-copy.db"
db = Database(db_path)

print("=== Observations (revived detectors) ===")
for observation in detect_observations(db, Path("/tmp/backtest-memory")):
    print(f"- {observation}")

print("\n=== Strength structural findings ===")
for finding in strength_structural_findings(db):
    print(f"- [{finding['key']}] {finding['statement']}")
    print(f"  evidence: {finding['evidence']}")

print("\n=== Strength profile block (as Riko would see it) ===")
print(strength_profile_block(db))
```

- [ ] **Step 2: Run against a fresh prod copy**

```bash
scp mini:projects/garmin-ai-coach/data/garmin.db /tmp/garmin-prod-copy.db && cd ~/projects/garmin-ai-coach && source .venv/bin/activate && python scripts/backtest_insights.py /tmp/garmin-prod-copy.db
```

Expected: real findings print — pull/push ratio ~4x, no-squat-pattern, 0% strength zone, plus revived observations. **STOP here and show the full output to Bodhi for review** — tone and correctness both. Adjust statements/gates per her feedback before deploying.

- [ ] **Step 3: Commit**

```bash
git add scripts/backtest_insights.py && git commit -m "Add insight backtest script for pre-deploy human review"
```

---

### Task 12: Deploy to mini + wire the Deep Review prompt

**Files:** none in repo — deployment and Riko config.

- [ ] **Step 1: Push, pull on mini, run tests there**

```bash
cd ~/projects/garmin-ai-coach && git push origin main && ssh mini 'cd ~/projects/garmin-ai-coach && git pull --ff-only && source .venv/bin/activate 2>/dev/null || true; pip install -e ".[dev]" -q && python -m pytest -q'
```

Expected: fast-forward pull, full suite green on mini. (If mini has no venv, check how `~/scripts/garmin-run.sh` invokes Python and install dev deps into that environment.)

- [ ] **Step 2: One manual sync and verify the store**

```bash
ssh mini '/Users/mini/scripts/garmin-run.sh -m src.main sync 2>&1 | tail -15 && sqlite3 ~/projects/garmin-ai-coach/data/garmin.db "SELECT key, status, discovered_date FROM insights ORDER BY id"'
```

Expected: sync completes normally (no state-machine regression), insights rows present, observations/strength findings printed in output.

- [ ] **Step 3: Verify the digest and push pipeline are untouched**

```bash
ssh mini 'ls -la ~/ai/data/signals/training-digest.txt ~/ai/data/training-pushed-* 2>/dev/null | tail -3'
```

Expected: digest still freshly written by subsequent */30 syncs; next morning's push flag appears as usual (check the morning after deploy).

- [ ] **Step 4: Add the card line to the Deep Review cron prompt**

Find the Saturday Training Deep Review cron and append one instruction to its prompt:

```bash
ssh mini 'openclaw cron list --json | jq -r ".[] | select(.name | test(\"Deep Review\")) | .id, .name, .enabled"'
```

Then edit that cron's prompt (via the OpenClaw config flow the other cron prompts use — check `~/.openclaw/cron/jobs.json` and the render-script contract in `project_riko_neve_morning_push.md` invariants) to add:

> If `~/ai/data/signals/insight-card.txt` exists and is dated within the last 7 days, include its content as a short "Did you know" section — recommendation-first, keep the evidence line.

**Verify after editing** (cron drift is a known hazard):

```bash
ssh mini 'openclaw cron show <ID> --json | jq ".enabled, .schedule"'
```

Expected: enabled/schedule unchanged from before the edit — only the prompt differs.

- [ ] **Step 5: Trigger: write the Phase 2 plan**

Phase 1 is live. Immediately invoke the superpowers:writing-plans skill for Phase 2 (spec §4.2 discovery miner, §4.3 illness early-warning composite + instant push, §4.5 basketball detectors, §6 monthly narrative, §7 feedback loop, §2 coach-contract prompt wiring). Do not close this work session without either writing that plan or explicitly recording with Bodhi a concrete trigger for when it happens.

---

## Self-review notes

- **Spec coverage (Phase 1 scope):** §4.1 → Task 3; §4.4 → Tasks 4-9; §5 → Task 2; §6-weekly → Task 10; §8 → Tasks 0, 12; §9 → Tasks 1-11 (TDD) + Task 11 (backtest gate). Phase 2 items intentionally deferred with trigger in Task 12 Step 5.
- **Known unknowns flagged inline:** `build_components` tuple shape (Task 9), `get_gym_sets` manual-set merge (Task 4), mini's Python environment (Task 12), Deep Review cron prompt mechanics (Task 12). Each has a verification step before the dependent change.
- **Type consistency:** `insert_insight(key, category, statement, evidence, status) -> bool`, `get_insights(status) -> list[dict]`, `mark_insight_surfaced(id)`, `_write_weekly_insight_card(db, card_path) -> bool` are used identically across Tasks 2, 3, 8, 10, 11.
