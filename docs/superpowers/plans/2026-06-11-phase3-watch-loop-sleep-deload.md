# Phase 3: Watch Loop, Sleep Rhythm, Deload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the daily watch push loop, passive sleep-rhythm detectors, and auto-applied deload weeks — completing spec `docs/superpowers/specs/2026-06-11-phase3-watch-loop-sleep-deload-design.md`.

**Architecture:** Same contract as Phases 1-2. F1 extends `cmd_push_workout` with calendar scheduling and rides the Training Push prompt. F2 adds sleep detectors to `discovery.py` + a rhythm section in the monthly narrative. F3 adds `deload.py` detection in `cmd_sync`, a directive file announced by the next morning push and honored by Saturday's Deep Review. No new push channels, no new crons.

**Tech Stack:** garminconnect (`schedule_workout` verified present), Python stdlib, pytest, OpenClaw cron prompt edits via the scp'd-script pattern.

**Verified facts (2026-06-11):**
- `garminconnect.Garmin` has `schedule_workout`, `unschedule_workout`, `get_scheduled_workouts`, `delete_workout` — exact signatures inspected at implementation (`python -c "import garminconnect, inspect; print(inspect.signature(garminconnect.Garmin.schedule_workout))"`).
- `cmd_push_workout` (src/main.py:923) uses `build_components` (correct — uploads need the real client), uploads via `upload_workout`, records into the workout tracker keyed by plan name.
- `daily_metrics.sleep_start` is an `"HH:MM"` string; Bodhi's real values span 01:25-04:16 — **midnight-crossing normalization is mandatory** (map to minutes since 18:00, mod 1440).
- Weekly load series: sum `db.get_corrected_load(activity_id, training_load)` over `get_recent_activities` bucketed by 7-day windows.
- Branch: `insight_pipeline_phase3` off main; deploy = mini `git pull` in the final task.

---

### Task 1: Calendar scheduling for pushed workouts

**Files:**
- Modify: `src/garmin/workout.py`, `src/main.py` (`cmd_push_workout`)
- Test: `test/test_workout_schedule.py` (date-payload logic only; the network call is verified live in Task 9)

- [ ] **Step 1: Inspect the live signature**

```bash
cd ~/projects/garmin-ai-coach && source .venv/bin/activate && python -c "import garminconnect, inspect; print(inspect.signature(garminconnect.Garmin.schedule_workout))"
```

Record the parameter shape (commonly `(workout_id, date)` with ISO date string). Adjust Step 3's wrapper to match exactly.

- [ ] **Step 2: Write failing test for the dedup guard**

`test/test_workout_schedule.py`:

```python
from src.garmin.workout import already_pushed_today, record_push


def test_same_day_dedup(tmp_path):
    assert already_pushed_today(tmp_path, "2026-06-12") is False
    record_push(tmp_path, "2026-06-12", workout_id="123", plan_name="Lower A")
    assert already_pushed_today(tmp_path, "2026-06-12") is True
    assert already_pushed_today(tmp_path, "2026-06-13") is False
```

Run: `python -m pytest test/test_workout_schedule.py -v` — expected FAIL.

- [ ] **Step 3: Implement scheduling + dedup in `src/garmin/workout.py`**

```python
def schedule_workout_on(client: GarminClient, workout_id: str, target_date: str) -> bool:
    """Schedule an uploaded workout on a calendar date (YYYY-MM-DD)."""
    try:
        client.client.schedule_workout(workout_id, target_date)  # match Step 1 signature
        logger.info("Workout %s scheduled for %s", workout_id, target_date)
        return True
    except Exception as e:
        logger.error("Failed to schedule workout %s: %s", workout_id, e)
        return False


PUSH_LOG_KEY = "daily_pushes"


def already_pushed_today(data_dir: Path, day: str) -> bool:
    tracker = load_workout_tracker(data_dir)
    return day in tracker.get(PUSH_LOG_KEY, {})


def record_push(data_dir: Path, day: str, workout_id: str, plan_name: str) -> None:
    tracker = load_workout_tracker(data_dir)
    tracker.setdefault(PUSH_LOG_KEY, {})[day] = {"workout_id": workout_id, "plan": plan_name}
    save_workout_tracker(data_dir, tracker)
```

- [ ] **Step 4: Wire into `cmd_push_workout`**

Add `--date` to the `push-workout` subparser (`push_parser.add_argument("--date", default=None, help="Schedule on date YYYY-MM-DD")`). In `cmd_push_workout`, after the successful-upload tracker block:

```python
        if args.date is not None:
            from .garmin.workout import already_pushed_today, record_push, schedule_workout_on
            if already_pushed_today(config.data_dir, args.date):
                print(f"Already pushed a workout for {args.date}; skipping schedule")
            elif schedule_workout_on(garmin_client, workout_id, args.date):
                record_push(config.data_dir, args.date, workout_id, plan.get("name", "unnamed"))
                print(f"Scheduled on watch calendar for {args.date}")
            else:
                print("WARNING: upload ok but scheduling failed — workout is in the library, not on the calendar")
```

- [ ] **Step 5: Run suite, commit**

```bash
python -m pytest -q
git add src/garmin/workout.py src/main.py test/test_workout_schedule.py && git commit -m "Add calendar scheduling and same-day dedup to push-workout"
```

---

### Task 2: Sleep-start normalization + late-night cost detector

**Files:**
- Modify: `src/ai/discovery.py`
- Test: `test/test_sleep_rhythm.py`

- [ ] **Step 1: Write failing tests**

`test/test_sleep_rhythm.py`:

```python
from src.ai.discovery import _sleep_start_minutes, discover_patterns


def test_sleep_start_normalization_crosses_midnight():
    assert _sleep_start_minutes("23:30") == 330      # 5.5h after 18:00
    assert _sleep_start_minutes("02:56") == 536      # next-day 02:56
    assert _sleep_start_minutes("18:00") == 0


def _seed_night(db, day, start, deep_min, score):
    db.upsert_daily_metrics({"date": day, "sleep_start": start,
                             "sleep_deep_min": deep_min, "sleep_score": score})


def test_late_night_cost_detected(db):
    # 16 normal nights (~01:00, deep 75) + 9 late nights (~03:00, deep 45).
    for index in range(16):
        _seed_night(db, f"2026-05-{index + 1:02d}", "01:00", 75, 80)
    for index in range(9):
        _seed_night(db, f"2026-06-{index + 1:02d}", "03:00", 45, 65)
    keys = {finding["key"] for finding in discover_patterns(db)}
    assert "sleep.late_night_deep_cost" in keys


def test_no_finding_when_bedtime_uniform(db):
    for index in range(25):
        _seed_night(db, f"2026-05-{index + 1:02d}", "01:00", 70 + index % 5, 78)
    keys = {finding["key"] for finding in discover_patterns(db)}
    assert "sleep.late_night_deep_cost" not in keys
```

Run to verify FAIL.

- [ ] **Step 2: Implement in `discovery.py`**

```python
SLEEP_WINDOW_DAYS = 60
LATE_NIGHT_THRESHOLD_MIN = 60


def _sleep_start_minutes(value: str) -> int:
    """'HH:MM' -> minutes since 18:00 (mod 24h), so 23:30 < 02:56 sorts sanely."""
    hours, minutes = value.split(":")
    return (int(hours) * 60 + int(minutes) - 18 * 60) % 1440


def _late_night_cost(db: Database, outcome_metric: str, days: int) -> dict[str, Any] | None:
    """Two-sample: nights ≥60min later than the personal median vs the rest."""
    nights = [
        (row, _sleep_start_minutes(row["sleep_start"]))
        for row in db.get_recent_metrics(days=days)
        if row.get("sleep_start") and row.get(outcome_metric) is not None
    ]
    if len(nights) < 2 * DISCOVERY_MIN_PAIRS:
        return None
    starts = sorted(minutes for _, minutes in nights)
    median_start = starts[len(starts) // 2]
    late = [row[outcome_metric] for row, minutes in nights
            if minutes - median_start >= LATE_NIGHT_THRESHOLD_MIN]
    normal = [row[outcome_metric] for row, minutes in nights
              if minutes - median_start < LATE_NIGHT_THRESHOLD_MIN]
    return gated_two_sample_effect(late, normal)
```

And inside `discover_patterns`, after the consecutive-day block:

```python
    late_cost = _late_night_cost(db, "sleep_deep_min", SLEEP_WINDOW_DAYS)
    if late_cost is not None and late_cost["delta"] < 0:
        findings.append({
            "key": "sleep.late_night_deep_cost",
            "statement": (
                f"On nights you fall asleep ≥1h later than your usual time, deep sleep"
                f" averages {abs(late_cost['delta']):.0f} min less"
                f" (n={late_cost['n_condition']} late vs {late_cost['n_comparison']} normal"
                f" nights, p={late_cost['p']})."
            ),
            "evidence": late_cost,
        })
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest test/test_sleep_rhythm.py -v && python -m pytest -q
git add src/ai/discovery.py test/test_sleep_rhythm.py && git commit -m "Add midnight-safe sleep normalization and late-night deep-sleep cost detector"
```

---

### Task 3: Bedtime consistency finding + optimal-window narrative section

**Files:**
- Modify: `src/ai/discovery.py`, `src/main.py` (`_write_monthly_narrative`)
- Test: extend `test/test_sleep_rhythm.py`

- [ ] **Step 1: Write failing tests (append to test_sleep_rhythm.py)**

```python
from src.ai.discovery import sleep_rhythm_block


def test_bedtime_inconsistency_fires_on_scatter(db):
    starts = ["23:30", "01:00", "03:30", "00:15", "02:45", "23:00", "04:00",
              "01:30", "03:00", "00:00", "02:00", "23:45", "03:45", "01:15"]
    for index, start in enumerate(starts):
        db.upsert_daily_metrics({"date": f"2026-06-{index + 1:02d}", "sleep_start": start,
                                 "sleep_score": 75})
    keys = {finding["key"] for finding in discover_patterns(db)}
    assert "sleep.bedtime_inconsistency" in keys


def test_rhythm_block_reports_best_window(db):
    for index in range(10):
        db.upsert_daily_metrics({"date": f"2026-05-{index + 1:02d}", "sleep_start": "01:00",
                                 "sleep_score": 85, "sleep_deep_min": 80})
    for index in range(10):
        db.upsert_daily_metrics({"date": f"2026-06-{index + 1:02d}", "sleep_start": "03:00",
                                 "sleep_score": 65, "sleep_deep_min": 50})
    block = sleep_rhythm_block(db)
    assert "01:00" in block
```

Run to verify FAIL.

- [ ] **Step 2: Implement**

In `discovery.py`:

```python
BEDTIME_STD_THRESHOLD_MIN = 75
BEDTIME_MIN_NIGHTS = 14
WINDOW_BUCKET_MIN = 30
WINDOW_MIN_BUCKET_N = 5


def _bedtime_consistency(db: Database, days: int = 28) -> dict[str, Any] | None:
    starts = [
        _sleep_start_minutes(row["sleep_start"])
        for row in db.get_recent_metrics(days=days)
        if row.get("sleep_start")
    ]
    if len(starts) < BEDTIME_MIN_NIGHTS:
        return None
    mean = sum(starts) / len(starts)
    std = (sum((s - mean) ** 2 for s in starts) / len(starts)) ** 0.5
    if std <= BEDTIME_STD_THRESHOLD_MIN:
        return None
    return {"std_min": round(std, 1), "n": len(starts), "window_days": days}


def sleep_rhythm_block(db: Database, days: int = SLEEP_WINDOW_DAYS) -> str:
    """Optimal sleep window by half-hour bucket — monthly narrative section."""
    buckets: dict[int, list[float]] = {}
    for row in db.get_recent_metrics(days=days):
        if not row.get("sleep_start") or row.get("sleep_score") is None:
            continue
        bucket = _sleep_start_minutes(row["sleep_start"]) // WINDOW_BUCKET_MIN
        buckets.setdefault(bucket, []).append(row["sleep_score"])
    qualified = {b: scores for b, scores in buckets.items() if len(scores) >= WINDOW_MIN_BUCKET_N}
    lines = ["## Sleep Rhythm (computed)"]
    if not qualified:
        lines.append("Not enough nights per bedtime bucket yet.")
        return "\n".join(lines)
    best = max(qualified, key=lambda b: sum(qualified[b]) / len(qualified[b]))
    start_min = (best * WINDOW_BUCKET_MIN + 18 * 60) % 1440
    end_min = (start_min + WINDOW_BUCKET_MIN) % 1440
    lines.append(
        f"Best-scoring bedtime window: {start_min // 60:02d}:{start_min % 60:02d}"
        f"-{end_min // 60:02d}:{end_min % 60:02d}"
        f" (avg sleep score {sum(qualified[best]) / len(qualified[best]):.0f},"
        f" n={len(qualified[best])} nights)"
    )
    for bucket in sorted(qualified):
        bucket_start = (bucket * WINDOW_BUCKET_MIN + 18 * 60) % 1440
        scores = qualified[bucket]
        lines.append(
            f"- {bucket_start // 60:02d}:{bucket_start % 60:02d}: avg score"
            f" {sum(scores) / len(scores):.0f} (n={len(scores)})"
        )
    return "\n".join(lines)
```

In `discover_patterns`, after the late-night block:

```python
    inconsistency = _bedtime_consistency(db)
    if inconsistency is not None:
        findings.append({
            "key": "sleep.bedtime_inconsistency",
            "statement": (
                f"Your bedtime varies ±{inconsistency['std_min']:.0f} min"
                f" (28d, n={inconsistency['n']} nights). Consistency is the single biggest"
                " lever on sleep quality — ahead of duration."
            ),
            "evidence": inconsistency,
        })
```

In `src/main.py::_write_monthly_narrative`, change the write to append the rhythm section:

```python
    from .ai.discovery import sleep_rhythm_block
    target.write_text(build_user_model(db) + "\n\n" + sleep_rhythm_block(db))
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest -q
git add src/ai/discovery.py src/main.py test/test_sleep_rhythm.py && git commit -m "Add bedtime consistency finding and optimal-window section in the monthly narrative"
```

---

### Task 4: Deload detection

**Files:**
- Create: `src/ai/deload.py`
- Test: `test/test_deload.py`

- [ ] **Step 1: Write failing tests**

`test/test_deload.py`:

```python
from datetime import date, timedelta

from src.ai.deload import deload_check


def _day(offset):
    return str(date.today() - timedelta(days=offset))


def _seed(db, *, week_loads, hrv7, hrv28):
    """week_loads[0] = most recent week. One activity per day carrying the load."""
    for week_index, weekly_total in enumerate(week_loads):
        for day_in_week in range(7):
            offset = week_index * 7 + day_in_week
            db.upsert_activity({
                "id": f"a{offset}", "date": _day(offset), "type": "strength",
                "duration_min": 60, "training_load": weekly_total / 7,
            })
    for offset in range(28):
        value = hrv7 if offset < 7 else hrv28
        db.upsert_daily_metrics({"date": _day(offset), "hrv_last_night": value,
                                 "training_readiness_score": 70})


def test_fires_on_rising_load_and_degraded_hrv(db):
    _seed(db, week_loads=[900, 750, 600, 450], hrv7=54.0, hrv28=60.0)
    result = deload_check(db)
    assert result is not None
    assert result["weekly_loads"][0] > result["weekly_loads"][1]


def test_silent_when_load_flat(db):
    _seed(db, week_loads=[600, 610, 590, 600], hrv7=54.0, hrv28=60.0)
    assert deload_check(db) is None


def test_silent_when_recovery_healthy(db):
    _seed(db, week_loads=[900, 750, 600, 450], hrv7=60.0, hrv28=60.0)
    assert deload_check(db) is None
```

Run to verify FAIL. (Note: tests use `date.today()` relative seeding because `get_recent_activities` filters on `date('now')` — fixed dates would age out.)

- [ ] **Step 2: Implement `src/ai/deload.py`**

```python
"""Week-granularity fatigue detection. Fires when load keeps climbing while
recovery markers degrade — the signal that a deload week is due."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..db.models import Database

DELOAD_WEEKS = 4
DELOAD_HRV_RATIO = 0.95
DELOAD_READINESS_DROP = 10.0
DELOAD_COOLDOWN_HOURS = 28 * 24


def _weekly_loads(db: Database) -> list[float]:
    """Corrected load per 7-day bucket, index 0 = most recent week."""
    buckets = [0.0] * DELOAD_WEEKS
    today = date.today()
    for activity in db.get_recent_activities(days=DELOAD_WEEKS * 7):
        days_ago = (today - date.fromisoformat(str(activity["date"]))).days
        bucket = min(days_ago // 7, DELOAD_WEEKS - 1)
        buckets[bucket] += db.get_corrected_load(
            activity["id"], activity.get("training_load") or 0.0
        )
    return [round(value, 1) for value in buckets]


def _mean(values: list[float]) -> float | None:
    cleaned = [v for v in values if v is not None]
    return sum(cleaned) / len(cleaned) if cleaned else None


def deload_check(db: Database) -> dict[str, Any] | None:
    """Evidence dict when a deload week is due, else None. Caller owns cooldown."""
    weekly = _weekly_loads(db)
    rising = all(weekly[i] > weekly[i + 1] for i in range(DELOAD_WEEKS - 1)) and weekly[-1] > 0
    if not rising:
        return None

    metrics = sorted(db.get_recent_metrics(days=28), key=lambda row: row["date"])
    hrv_recent = _mean([row.get("hrv_last_night") for row in metrics[-7:]])
    hrv_baseline = _mean([row.get("hrv_last_night") for row in metrics])
    readiness_recent = _mean([row.get("training_readiness_score") for row in metrics[-7:]])
    readiness_baseline = _mean([row.get("training_readiness_score") for row in metrics])

    hrv_degraded = (
        hrv_recent is not None and hrv_baseline is not None
        and hrv_recent <= DELOAD_HRV_RATIO * hrv_baseline
    )
    readiness_degraded = (
        readiness_recent is not None and readiness_baseline is not None
        and readiness_recent <= readiness_baseline - DELOAD_READINESS_DROP
    )
    if not (hrv_degraded or readiness_degraded):
        return None

    return {
        "weekly_loads": weekly,
        "hrv_recent": round(hrv_recent, 1) if hrv_recent is not None else None,
        "hrv_baseline": round(hrv_baseline, 1) if hrv_baseline is not None else None,
        "readiness_recent": round(readiness_recent, 1) if readiness_recent is not None else None,
        "readiness_baseline": round(readiness_baseline, 1) if readiness_baseline is not None else None,
    }
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest test/test_deload.py -v && python -m pytest -q
git add src/ai/deload.py test/test_deload.py && git commit -m "Add deload detection from rising load and degraded recovery markers"
```

---

### Task 5: Deload directive — write, announce, apply

**Files:**
- Modify: `src/main.py` (`_write_deload_directive`, cmd_sync hook, digest line)
- Test: `test/test_deload_directive.py`

- [ ] **Step 1: Write failing test**

`test/test_deload_directive.py`:

```python
from src.main import _write_deload_directive


def test_directive_written_with_cooldown(db, tmp_path):
    evidence = {"weekly_loads": [900.0, 750.0, 600.0, 450.0],
                "hrv_recent": 54.0, "hrv_baseline": 60.0,
                "readiness_recent": 62.0, "readiness_baseline": 71.0}
    target = tmp_path / "deload-directive.txt"

    assert _write_deload_directive(db, evidence, target) is True
    content = target.read_text()
    assert "40-50%" in content and "900" in content
    assert len(db.get_insights()) == 1               # audit-trail row
    # Cooldown: second fire inside 28 days is suppressed.
    assert _write_deload_directive(db, evidence, target) is False
```

Run to verify FAIL.

- [ ] **Step 2: Implement in `src/main.py`**

```python
def _write_deload_directive(db, evidence: dict, target_path: Path | None = None) -> bool:
    """Persist the deload decision for the digest and Saturday Deep Review."""
    from datetime import date
    from .ai.deload import DELOAD_COOLDOWN_HOURS
    if db.hours_since_last_notification("deload") < DELOAD_COOLDOWN_HOURS:
        return False
    target = target_path or (Path.home() / "ai" / "data" / "signals" / "deload-directive.txt")
    iso_week = date.today().isocalendar()
    lines = [
        f"# Deload Directive — issued {date.today()} (apply to next week)",
        "",
        "Action: cut next week's training volume 40-50%. Keep movement quality work",
        "(home micro-sessions, mobility); no progression jumps; basketball at RPE<=6.",
        "",
        f"Evidence: weekly corrected loads {evidence['weekly_loads']} (rising 3+ weeks);"
        f" 7d HRV {evidence['hrv_recent']} vs 28d {evidence['hrv_baseline']};"
        f" 7d readiness {evidence['readiness_recent']} vs 28d {evidence['readiness_baseline']}.",
        "",
        "Veto: Bodhi can cancel by telling Riko — then delete this file.",
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n")
    db.add_notification("deload", str(date.today()))
    db.insert_insight(
        key=f"deload.applied_{iso_week.year}_{iso_week.week:02d}",
        category="deload",
        statement=f"Deload week issued for ISO week {iso_week.week + 1}: " + lines[2],
        evidence=evidence,
        status="surfaced",  # announced via morning push, not the Saturday card queue
    )
    return True
```

- [ ] **Step 3: Hook into cmd_sync (after the discovery block)**

```python
    # Week-granularity fatigue check — directive announced by next morning push.
    from .ai.deload import deload_check
    try:
        deload_evidence = deload_check(sync.db)
        if deload_evidence is not None and _write_deload_directive(sync.db, deload_evidence):
            print("Deload directive issued")
    except Exception as error:
        logger.warning("Deload check failed: %s", error)
```

- [ ] **Step 4: Surface in the digest**

Read `_write_training_digest` (src/main.py:122-161), then append before the final `digest_path.write_text(digest)`:

```python
    deload_path = Path.home() / "ai" / "data" / "signals" / "deload-directive.txt"
    if deload_path.exists():
        import time
        if (time.time() - deload_path.stat().st_mtime) < 8 * 86400:
            digest += "\n## DELOAD DIRECTIVE (active — lead the push with this)\n"
            digest += deload_path.read_text()
```

- [ ] **Step 5: Run suite, commit**

```bash
python -m pytest -q
git add src/main.py test/test_deload_directive.py && git commit -m "Issue deload directives from cmd_sync and surface them in the training digest

Detection fires at most once per 28 days, writes a machine-readable directive that the next morning push leads with and Saturday's Deep Review applies to the weekly plan. An insight row records the audit trail; veto is deleting the file via Riko."
```

---

### Task 6: Riko prompt wiring (Training Push + Deep Review)

**Files:** none in repo — two OpenClaw cron prompts on mini (scp'd-script pattern, drift verify after).

- [ ] **Step 1: Training Push prompt — watch push + deload instructions**

Patch script inserts (after the COACH CONTRACT block) — exact text:

```
=== WATCH PUSH (strength days only) ===
If today's decision (option A) is a gym/strength session: build the structured plan JSON exactly per the workout_structured schema, using ONLY loads/reps from the Exercise Progression Layer. Then run:
  cd ~/projects/garmin-ai-coach && .venv/bin/python -m src.main push-workout '<json>' --date $(date +%F)
If the command prints "Scheduled on watch calendar", add one line to your report: "已推到手表". If it fails, do NOT retry more than once; include the plan inline and say the watch push failed. Never let this step delay or replace the report itself.

=== DELOAD DIRECTIVE ===
If the digest contains a "DELOAD DIRECTIVE (active)" section: lead the push with the deload decision and its evidence, per the Coach Contract (state the call, cite the numbers, mention she can veto by telling you). Scale today's recommendation accordingly.
```

- [ ] **Step 2: Deep Review prompt — apply the directive**

Insert as data source 10:

```
10. `~/ai/data/signals/deload-directive.txt` — if it exists and was issued within the last 8 days, next week's plan in weekly-plan.md MUST be a deload week: total volume cut 40-50% vs this week, no load/rep progression, keep home micro-sessions and mobility, basketball capped at RPE 6. Name the deload explicitly in both output files. If Bodhi has vetoed (file deleted), plan normally.
```

- [ ] **Step 3: Apply both patches and verify drift invariants**

Same mechanics as Phase 2 Task 9 (dump → scp'd python patch → `cron edit --message` → verify). Expected after: Training Push `enabled: false` unchanged, Deep Review `enabled: true` + `0 9 * * 6` unchanged, both prompts contain the new sections.

---

### Task 7: Backtest + review gate

- [ ] **Step 1: Extend `scripts/backtest_insights.py`**

```python
print("\n=== Sleep rhythm ===")
from src.ai.discovery import sleep_rhythm_block
print(sleep_rhythm_block(db))

print("\n=== Deload check — would it fire today? ===")
from src.ai.deload import deload_check
deload = deload_check(db)
print(deload if deload else "No deload due (load not rising 3+ weeks with degraded recovery)")
```

- [ ] **Step 2: Run against a fresh prod copy; STOP for Bodhi's review**

```bash
scp mini:projects/garmin-ai-coach/data/garmin.db /tmp/garmin-p3.db && python scripts/backtest_insights.py /tmp/garmin-p3.db
```

Show Bodhi: any new sleep findings (with her real numbers), the optimal-window table, and whether deload would fire right now. Tune statements/thresholds per feedback. Commit the script.

---

### Task 8: Deploy + live watch-push verification

- [ ] **Step 1: Merge, push, pull on mini, tests there** (same commands as Phase 2).

- [ ] **Step 2: Live watch-push smoke test on mini (real Garmin, throwaway workout)**

```bash
ssh mini 'cd ~/projects/garmin-ai-coach && .venv/bin/python -m src.main push-workout "{\"name\": \"P3 schedule smoke test\", \"exercises\": [{\"name\": \"Glute Bridge Hold\", \"sets\": 1, \"reps\": 5}]}" --date 2026-06-13'
```

Then verify and clean up:

```bash
ssh mini 'cd ~/projects/garmin-ai-coach && .venv/bin/python -c "
from src.main import build_components
_, _, client, _, _, _ = build_components(None)
scheduled = client.client.get_scheduled_workouts(\"2026-06-13\", \"2026-06-13\")
print(scheduled)
"'
# unschedule + delete the throwaway via the matching client methods, verify gone
```

(Exact get/unschedule signatures from Task 1 Step 1's inspection; adapt. The smoke workout must be deleted before closing the task.)

- [ ] **Step 3: One manual sync; verify no regression**

Digest still written, insights intact, no deload directive on healthy data (or review it if it fires for real).

- [ ] **Step 4: Apply Task 6 prompt patches, verify, update memory**

Update `project_garmin_insight_pipeline.md`: Phase 3 live, watch-push contract, deload veto path, sleep detectors. Tell Bodhi what changes tomorrow morning: strength days land on the watch; a deload, when due, announces itself; sleep patterns will start appearing in Saturday cards as gates pass.

---

## Self-review notes

- **Spec coverage:** F1 → Tasks 1, 6, 8; F2 → Tasks 2, 3, 7; F3 → Tasks 4, 5, 6; testing/backtest contract → Tasks 7-8.
- **Known unknowns flagged:** `schedule_workout` signature (Task 1 Step 1 inspects before wiring); `get_scheduled_workouts`/unschedule shapes (Task 8); `_write_training_digest` body read before the append (Task 5 Step 4).
- **Type consistency:** `deload_check(db) -> dict|None` consumed by `_write_deload_directive(db, evidence, path) -> bool`; `_sleep_start_minutes(str) -> int` used by both sleep detectors; `already_pushed_today/record_push` share the tracker file via existing load/save helpers.
- **Safety:** watch push is best-effort and report-first; deload cooldown 28d; sleep features add zero pushes.
