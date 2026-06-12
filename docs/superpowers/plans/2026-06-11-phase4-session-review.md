# Phase 4: Post-Session Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every real training session gets one proactive review push (data audit + next-step + one optional feedback question), with a 2-hour correction buffer for strength sessions, superseding the old Training Follow-Up.

**Architecture:** A new pure-Python module (`session_review.py`) computes due-ness and per-type review blocks inside `cmd_sync`; a managed prompt ("Riko Session Review", externally triggered, registered in `sync-cron-prompts.sh`) presents them. Spec: `docs/superpowers/specs/2026-06-11-phase4-session-review-design.md`.

**Verified facts:** `activities.start_time` format is `"YYYY-MM-DD HH:MM:SS"`; `db.get_last_notification(type)` / `add_notification` provide per-activity dedup; `refresh_recent_gym_sets` pulls her Garmin Connect edits every sync; prompt edits go through `~/ai/prompts/*.md` + `sync-cron-prompts.sh` (NEVER direct cron edit); branch `insight_pipeline_phase4`.

---

### Task 1: Due-ness and pending-review selection

**Files:**
- Create: `src/ai/session_review.py`
- Test: `test/test_session_review_due.py`

- [ ] **Step 1: Write failing tests**

`test/test_session_review_due.py`:

```python
from datetime import datetime, timedelta

from src.ai.session_review import pending_reviews

from test.conftest import make_set, seed_strength_activity


def _now():
    return datetime.now()


def _seed_activity(db, activity_id, activity_type, hours_ago, duration_min=60):
    start = _now() - timedelta(hours=hours_ago, minutes=duration_min)
    db.upsert_activity({
        "id": activity_id,
        "date": str(start.date()),
        "type": activity_type,
        "duration_min": duration_min,
        "training_load": 100,
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
    })


def test_basketball_due_immediately_strength_waits(db):
    _seed_activity(db, "bb", "basketball", hours_ago=0.5)
    _seed_activity(db, "st", "strength", hours_ago=0.5)
    due_ids = {a["id"] for a in pending_reviews(db, now=_now())}
    assert "bb" in due_ids          # no buffer for non-strength
    assert "st" not in due_ids      # 2h buffer still running


def test_strength_due_after_buffer(db):
    _seed_activity(db, "st", "strength", hours_ago=2.5)
    assert {a["id"] for a in pending_reviews(db, now=_now())} == {"st"}


def test_walking_and_short_sessions_never_due(db):
    _seed_activity(db, "walk", "walking", hours_ago=5)
    _seed_activity(db, "tiny", "basketball", hours_ago=5, duration_min=10)
    assert pending_reviews(db, now=_now()) == []


def test_reviewed_activity_not_pending_again(db):
    _seed_activity(db, "bb", "basketball", hours_ago=1)
    db.add_notification("session_review_bb", "sent")
    assert pending_reviews(db, now=_now()) == []
```

Run: `python -m pytest test/test_session_review_due.py -v` — expected FAIL (module missing).

- [ ] **Step 2: Implement**

`src/ai/session_review.py`:

```python
"""Post-session review — computes due-ness and per-type review blocks.

Strength sessions wait STRENGTH_BUFFER_HOURS after the session ends because
Bodhi corrects sets/weights in Garmin Connect within 1-2 hours; other types
carry no manually-edited data and review on the next sync."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..db.models import Database

EXCLUDED_TYPES = {"walking"}
MIN_DURATION_MIN = 15
STRENGTH_BUFFER_HOURS = 2.0
LOOKBACK_DAYS = 3


def _activity_end(activity: dict[str, Any]) -> datetime | None:
    start_raw = activity.get("start_time")
    if not start_raw:
        return None
    try:
        start = datetime.fromisoformat(str(start_raw))
    except ValueError:
        return None
    return start + timedelta(minutes=activity.get("duration_min") or 0)


def _buffer_hours(activity_type: str) -> float:
    return STRENGTH_BUFFER_HOURS if activity_type == "strength" else 0.0


def pending_reviews(db: Database, now: datetime | None = None) -> list[dict[str, Any]]:
    """Reviewable, unreviewed, due activities — oldest first."""
    now = now or datetime.now()
    due: list[dict[str, Any]] = []
    for activity in db.get_recent_activities(days=LOOKBACK_DAYS):
        activity_type = str(activity.get("type") or "")
        if activity_type in EXCLUDED_TYPES or activity_type == "":
            continue
        if (activity.get("duration_min") or 0) < MIN_DURATION_MIN:
            continue
        if db.get_last_notification(f"session_review_{activity['id']}") is not None:
            continue
        end = _activity_end(activity)
        if end is None:
            continue
        if now >= end + timedelta(hours=_buffer_hours(activity_type)):
            due.append(activity)
    return sorted(due, key=lambda a: str(a.get("start_time")))
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest test/test_session_review_due.py -v && python -m pytest -q
git add src/ai/session_review.py test/test_session_review_due.py && git commit -m "Add session review due-ness with strength correction buffer"
```

---

### Task 2: Per-type review blocks

**Files:**
- Modify: `src/ai/session_review.py`
- Test: `test/test_session_review_blocks.py`

- [ ] **Step 1: Write failing tests**

`test/test_session_review_blocks.py`:

```python
from test.conftest import make_set, seed_strength_activity

from src.ai.session_review import review_block


def test_strength_block_includes_sets_and_pr(db):
    # history: RDL best e1RM from 45lb x10; new session: 50lb x10 ⇒ PR
    seed_strength_activity(db, "old", "2026-05-20", [make_set("Romanian Deadlift", 10, 45.0)])
    seed_strength_activity(db, "new", "2026-06-10", [
        make_set("Romanian Deadlift", 10, 50.0),
        make_set("Lat Pulldown", 12, 70.0),
    ])
    activity = next(a for a in db.get_recent_activities(days=90, activity_type="strength")
                    if a["id"] == "new")
    block = review_block(db, activity)
    assert "Romanian Deadlift" in block
    assert "PR" in block
    assert "ASK_FEEDBACK: yes" in block      # no RPE recorded for this session


def test_feedback_present_suppresses_question(db):
    seed_strength_activity(db, "s1", "2026-06-10", [make_set("Lat Pulldown", 12, 70.0)])
    db.insert_training_feedback("s1", rpe=7, pain_area=None, pain_level=None,
                                menstrual_symptoms=None, notes="solid")
    activity = db.get_recent_activities(days=90, activity_type="strength")[0]
    block = review_block(db, activity)
    assert "ASK_FEEDBACK: no" in block
```

Run to verify FAIL. Note: check `insert_training_feedback`'s exact signature at `src/db/models.py:468` and align the test call — keyword names above are a guess; the schema columns are `rpe, pain_area, pain_level, menstrual_symptoms, notes`.

- [ ] **Step 2: Implement**

Append to `src/ai/session_review.py`:

```python
def _typical_comparison(db: Database, activity: dict[str, Any]) -> str:
    """Load/duration vs the 90-day mean for this activity type."""
    same_type = [
        a for a in db.get_recent_activities(days=90, activity_type=str(activity.get("type")))
        if a["id"] != activity["id"] and (a.get("training_load") or 0) > 0
    ]
    if len(same_type) < 3:
        return ""
    mean_load = sum(a["training_load"] for a in same_type) / len(same_type)
    load = activity.get("training_load") or 0
    if mean_load <= 0 or load <= 0:
        return ""
    ratio = load / mean_load
    return f"Load {load:.0f} = {ratio * 100:.0f}% of your typical {activity.get('type')} session (n={len(same_type)})"


def _strength_details(db: Database, activity: dict[str, Any]) -> list[str]:
    from .strength_profile import e1rm
    lines: list[str] = []
    # prior best e1RM per exercise, excluding this session
    prior_best: dict[str, float] = {}
    for other in db.get_recent_activities(days=365, activity_type="strength"):
        if other["id"] == activity["id"]:
            continue
        for set_row in db.get_gym_sets(other["id"]):
            weight, reps = set_row.get("weight_lb"), set_row.get("reps")
            exercise = str(set_row.get("exercise") or "")
            if weight and reps and exercise:
                prior_best[exercise] = max(prior_best.get(exercise, 0.0), e1rm(weight, reps))

    session_sets: dict[str, list[str]] = {}
    session_best: dict[str, float] = {}
    for set_row in db.get_gym_sets(activity["id"]):
        exercise = str(set_row.get("exercise") or "")
        if not exercise:
            continue
        weight, reps = set_row.get("weight_lb"), set_row.get("reps")
        session_sets.setdefault(exercise, []).append(
            f"{reps}x{weight:.0f}lb" if weight else f"{reps} reps"
        )
        if weight and reps:
            session_best[exercise] = max(session_best.get(exercise, 0.0), e1rm(weight, reps))

    for exercise, sets in session_sets.items():
        pr_marker = ""
        if exercise in session_best and session_best[exercise] > prior_best.get(exercise, 0.0) > 0:
            pr_marker = "  <-- PR (new best e1RM)"
        lines.append(f"- {exercise}: {', '.join(sets)}{pr_marker}")
    return lines


def _basketball_details(db: Database, activity: dict[str, Any]) -> list[str]:
    from pathlib import Path
    from .basketball_profile import hr_drift_pct, zone45_share
    lines: list[str] = []
    fit_path = activity.get("fit_file_path")
    if fit_path and Path(str(fit_path)).exists():
        try:
            from ..garmin.fit_parser import parse_hr_series
            drift = hr_drift_pct(parse_hr_series(fit_path))
            if drift is not None:
                lines.append(f"- HR drift 2nd half vs 1st: {drift:+.1f}% (conditioning fade proxy)")
        except Exception:
            pass
    shares = zone45_share(db, days=90)
    this_one = [s for s in shares if s["date"] == str(activity.get("date"))]
    if this_one:
        lines.append(f"- Zone 4-5 share: {this_one[-1]['share']:.0%}")
    return lines


def _ski_details(db: Database, activity: dict[str, Any]) -> list[str]:
    runs = db.get_ski_runs(activity["id"])
    if not runs:
        return []
    speeds = [run.get("avg_speed") for run in runs if run.get("avg_speed")]
    lines = [f"- {len(runs)} runs"]
    if speeds:
        lines.append(f"- avg speed {sum(speeds) / len(speeds):.1f}, fastest run #{speeds.index(max(speeds)) + 1}")
    return lines


def _needs_feedback(db: Database, activity: dict[str, Any]) -> bool:
    return db.get_training_feedback(activity["id"]) is None


def review_block(db: Database, activity: dict[str, Any]) -> str:
    """One computed review block. The LLM presents; it never recomputes."""
    activity_type = str(activity.get("type") or "")
    lines = [
        f"## Session Review — {activity.get('date')} {activity_type}"
        f" ({activity.get('duration_min', 0):.0f} min, load {activity.get('training_load') or 0:.0f})",
    ]
    comparison = _typical_comparison(db, activity)
    if comparison:
        lines.append(comparison)

    if activity_type == "strength":
        lines.extend(_strength_details(db, activity))
    elif activity_type == "basketball":
        lines.extend(_basketball_details(db, activity))
    elif activity_type == "skiing":
        lines.extend(_ski_details(db, activity))

    lines.append(f"ASK_FEEDBACK: {'yes' if _needs_feedback(db, activity) else 'no'}")
    return "\n".join(lines)
```

- [ ] **Step 3: Run tests, full suite, commit**

```bash
python -m pytest test/test_session_review_blocks.py -v && python -m pytest -q
git add src/ai/session_review.py test/test_session_review_blocks.py && git commit -m "Add per-type session review blocks with PR detection and feedback flag"
```

---

### Task 3: Signal writer + trigger + cmd_sync hook (supersedes Follow-Up)

**Files:**
- Modify: `src/main.py`
- Test: `test/test_session_review_channel.py`

- [ ] **Step 1: Write failing test**

`test/test_session_review_channel.py`:

```python
from datetime import datetime, timedelta

from src.main import _write_session_reviews

from test.conftest import make_set, seed_strength_activity


def test_writes_blocks_and_marks_reviewed(db, tmp_path):
    start = datetime.now() - timedelta(hours=3)
    db.upsert_activity({
        "id": "bb1", "date": str(start.date()), "type": "basketball",
        "duration_min": 60, "training_load": 150,
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
    })
    target = tmp_path / "session-review.txt"

    wrote = _write_session_reviews(db, target)

    assert wrote is True
    assert "basketball" in target.read_text()
    # marked reviewed ⇒ second call writes nothing
    assert _write_session_reviews(db, target) is False
```

Run to verify FAIL.

- [ ] **Step 2: Implement writer + trigger in `src/main.py`**

Next to the other signal writers:

```python
DEFAULT_RIKO_SESSION_REVIEW_CRON_NAME = "Riko Session Review"


def _write_session_reviews(db, target_path: Path | None = None) -> bool:
    """Write review blocks for all due sessions; mark them reviewed.
    Returns True when there is something for Riko to present."""
    from datetime import date as date_type
    from .ai.session_review import pending_reviews, review_block
    due = pending_reviews(db)
    if not due:
        return False
    target = target_path or (Path.home() / "ai" / "data" / "signals" / "session-review.txt")
    blocks = [f"# Session Review — generated {date_type.today()}", ""]
    for activity in due:
        blocks.append(review_block(db, activity))
        blocks.append("")
        db.add_notification(f"session_review_{activity['id']}", "sent")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(blocks))
    return True


def _trigger_riko_session_review() -> bool:
    """Mirror of _trigger_riko_health_alert for the session review cron."""
    import shutil
    import subprocess
    cron_path = os.pathsep.join([
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
        os.environ.get("PATH", ""),
    ])
    openclaw_bin = shutil.which("openclaw", path=cron_path) or "/opt/homebrew/bin/openclaw"
    cron_id = os.environ.get("RIKO_SESSION_REVIEW_CRON_ID") or _openclaw_cron_id_by_name(
        openclaw_bin, cron_path, DEFAULT_RIKO_SESSION_REVIEW_CRON_NAME
    )
    if cron_id is None:
        print("WARNING: Riko Session Review cron not found; review file written but not pushed")
        return False
    try:
        result = subprocess.run(
            [openclaw_bin, "cron", "run", cron_id],
            env={**os.environ, "PATH": cron_path},
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"Riko session review triggered (cron {cron_id})")
            return True
        print(f"WARNING: session review trigger failed: {result.stderr[:200]}")
    except Exception as error:
        print(f"WARNING: session review trigger failed: {error}")
    return False
```

- [ ] **Step 3: Hook into cmd_sync and retire the Follow-Up trigger**

In `cmd_sync`, REPLACE the existing line `_maybe_trigger_training_followup(sync, dry_run=False)` with:

```python
    # Post-session reviews (supersede the old Training Follow-Up — the review
    # itself asks the one feedback question when data is missing).
    try:
        if _write_session_reviews(sync.db):
            _trigger_riko_session_review()
    except Exception as error:
        logger.warning("Session review failed: %s", error)
```

Leave `_maybe_trigger_training_followup` itself and the Follow-Up cron in place (dormant). Also grep for other callers (`grep -n "_maybe_trigger_training_followup" src/main.py`) — `_run_reflect` also calls it; leave that one (reflect has no cron; harmless).

- [ ] **Step 4: Run suite, commit**

```bash
python -m pytest -q
git add src/main.py test/test_session_review_channel.py && git commit -m "Write and trigger post-session reviews from cmd_sync, superseding the Follow-Up trigger"
```

---

### Task 4: Backtest — generate reviews for her 5 most recent real sessions

- [ ] **Step 1: Extend `scripts/backtest_insights.py`**

```python
print("\n=== Session reviews (5 most recent real sessions) ===")
from src.ai.session_review import EXCLUDED_TYPES, MIN_DURATION_MIN, review_block

recent_real = [
    a for a in db.get_recent_activities(days=60)
    if str(a.get("type")) not in EXCLUDED_TYPES
    and (a.get("duration_min") or 0) >= MIN_DURATION_MIN
][:5]
for activity in recent_real:
    print()
    print(review_block(db, activity))
```

- [ ] **Step 2: Run on a fresh prod copy; STOP and show Bodhi**

```bash
scp mini:projects/garmin-ai-coach/data/garmin.db /tmp/garmin-p4.db && python scripts/backtest_insights.py /tmp/garmin-p4.db 2>/dev/null | sed -n '/Session reviews/,$p'
```

Review for correctness AND tone-source quality (these blocks feed the push). Tune per feedback. Commit.

---

### Task 5: Managed prompt + deploy

- [ ] **Step 1: Create `~/ai/prompts/session-review.md` on mini (scp'd file, not heredoc)**

Content:

```
Read ~/ai/data/signals/session-review.txt (computed post-session review from the Garmin backend; numbers are pre-computed — never invent or recompute them).

Write Bodhi's post-session review for Telegram. Form: max 7 lines, no headers, no schema labels, no markdown tables.
- Line 1: the session verdict — lead with the most notable computed fact (a PR gets celebrated; a fade gets named plainly).
- 2-3 data facts max, chosen for coaching value, numbers exactly as computed.
- One concrete next-time line derived from the data.
- If the block says ASK_FEEDBACK: yes, end with ONE short question (RPE / pain / how it felt). If she answers later, record it via the training feedback path. If ASK_FEEDBACK: no, no question.
Multiple session blocks in the file = one combined message, most recent session first.

=== COACH CONTRACT ===
(same block as the other training prompts — copy verbatim from body-status-push.md)
```

Register in `~/ai/scripts/sync-cron-prompts.sh` next to the other manual jobs:

```bash
upsert_manual_delivered_job "Riko Session Review" "$PROMPTS/session-review.md" "0 3 1 1 *" 600
```

Run `bash ~/ai/scripts/sync-cron-prompts.sh`, verify the new cron exists disabled with the prompt, and commit the `~/ai` repo (its conventions: `feat:` prefix).

- [ ] **Step 2: Deploy code, end-to-end verify**

Merge branch → push → mini `git pull` → pytest on mini → run one manual sync. If a recent unreviewed session exists, the review fires for real — check the Telegram output quality. Verify the morning-push pipeline files unaffected and `session_review_*` notifications recorded.

- [ ] **Step 3: Update memory + close**

Update `project_garmin_insight_pipeline.md`: Phase 4 live (trigger chain, 2h strength buffer rationale, Follow-Up superseded, new cron name). Tell Bodhi the loop: train → save on watch → (strength: correct within 2h) → review lands.

---

## Self-review notes

- **Spec coverage:** §2 trigger+buffer → Tasks 1, 3; §3 blocks → Task 2; §3 follow-up merge → Task 3 Step 3; §4 prompt → Task 5; §6 backtest gate → Task 4.
- **Known unknowns flagged:** `insert_training_feedback` signature (Task 2 Step 1), `get_ski_runs` row keys (`avg_speed` — verify at implementation against models.py:530), reflect's follow-up call left untouched.
- **Type consistency:** `pending_reviews(db, now) -> list[dict]` consumed by `_write_session_reviews(db, path) -> bool`; `review_block(db, activity) -> str` shared by writer and backtest.
