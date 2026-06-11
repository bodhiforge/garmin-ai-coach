# Insight Phase 2: Discovery Miner, Early Warnings, Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the correlation discovery miner, illness/overreach instant warnings, basketball detectors, monthly narrative, and the adopted-insight feedback loop — completing spec §4.2, §4.3, §4.5, §6-monthly, §7, §2-wiring.

**Architecture:** Same contract as Phase 1 — pure-Python detectors inside `cmd_sync`, statistical gates before anything is stored, the LLM only presents. New pieces: a paired/two-sample permutation-test engine (`discovery.py`), a 4-signal warning composite with an instant Riko push channel (new disabled OpenClaw cron, externally triggered like the Training Push), evidence re-validation so numbers refresh as data grows, and an `insight` CLI whose `adopt` status feeds personalized recovery costs back into the coach layer.

**Tech Stack:** Python stdlib (`random` for permutation tests, seeded — deterministic), fitdecode (already a dependency) for basketball HR series, pytest, OpenClaw cron CLI on mini.

**Prerequisites:** Phase 1 deployed (insights table at schema v6, `cmd_sync` hooks, Saturday card, Deep Review prompt sources 8-9). Production caution unchanged: develop on macp branch `insight_pipeline_phase2`, deploy on mini via `git pull` in the final task only.

**Known facts (verified 2026-06-11):**
- Riko trigger pattern: `_trigger_riko_analysis()` runs `openclaw cron run <id>`; id resolves via `os.environ.get("RIKO_..._CRON_ID", DEFAULT_...)`; `_openclaw_cron_id_by_name()` exists for name lookup.
- Cooldowns: `db.add_notification(type, content)` + `db.hours_since_last_notification(type)` (returns 999.0 when none).
- Baseline helper `_stats(values) -> (mean, std)` exists in `src/ai/anomaly.py:28`.
- `fit_parser.py` has gym/ski parsers only — no generic HR series; fitdecode record frames carry `timestamp` and `heart_rate`.
- `activities` columns include `start_time`, `hr_zone1_sec`..`hr_zone5_sec`, `fit_file_path`, `training_load`; `daily_metrics` include `respiration_avg`, `resting_hr`, `hrv_last_night`, `sleep_score`, `sleep_deep_min`, `training_readiness_score`, `menstrual_phase`.
- Editing OpenClaw cron prompts: dump via `openclaw cron show <id> --json | jq -r '.payload.message'`, patch with a **scp'd Python script** (remote heredocs expand backticks — known hazard), write back with `openclaw cron edit <id> --message "$(cat file)"`, verify `enabled`/`schedule` unchanged.

---

### Task 1: discovery.py — permutation-test engine

**Files:**
- Create: `src/ai/discovery.py`
- Test: `test/test_discovery_engine.py`

- [ ] **Step 1: Write failing tests**

`test/test_discovery_engine.py`:

```python
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
```

Run: `python -m pytest test/test_discovery_engine.py -v` — expected FAIL (module missing).

- [ ] **Step 2: Implement the engine**

`src/ai/discovery.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify pass**

```bash
cd ~/projects/garmin-ai-coach && source .venv/bin/activate && python -m pytest test/test_discovery_engine.py -v
```

Expected: 4 PASS.

- [ ] **Step 4: Commit**

```bash
git add src/ai/discovery.py test/test_discovery_engine.py && git commit -m "Add discovery engine with paired and two-sample permutation gates"
```

---

### Task 2: Discovery detectors over real metric pairs

**Files:**
- Modify: `src/ai/discovery.py`
- Test: `test/test_discovery_detectors.py`

- [ ] **Step 1: Write failing tests**

`test/test_discovery_detectors.py`:

```python
from src.ai.discovery import discover_patterns

from test.conftest import make_set, seed_strength_activity


def _seed_hrv(db, day, value):
    db.upsert_daily_metrics({"date": day, "hrv_last_night": value,
                             "resting_hr": 55, "training_readiness_score": 70})


def test_activity_next_day_hrv_drop_detected(db):
    """10 basketball sessions, HRV drops ~12% the morning after each ⇒ finding."""
    for index in range(10):
        before = f"2026-04-{2 * index + 1:02d}"
        game = f"2026-04-{2 * index + 1:02d}"
        after = f"2026-04-{2 * index + 2:02d}"
        _seed_hrv(db, before, 60.0)
        _seed_hrv(db, after, 52.5)
        db.upsert_activity({"id": f"b{index}", "date": game, "type": "basketball",
                            "duration_min": 90, "training_load": 150})
    findings = discover_patterns(db)
    keys = {finding["key"] for finding in findings}
    assert "discovery.basketball_next_day_hrv" in keys
    finding = next(f for f in findings if f["key"] == "discovery.basketball_next_day_hrv")
    assert finding["evidence"]["n"] == 10
    assert finding["evidence"]["relative_effect"] < -0.05


def test_no_finding_without_consistent_effect(db):
    """Alternating HRV response ⇒ gate stays closed."""
    for index in range(10):
        day = f"2026-04-{2 * index + 1:02d}"
        after = f"2026-04-{2 * index + 2:02d}"
        _seed_hrv(db, day, 60.0)
        _seed_hrv(db, after, 66.0 if index % 2 == 0 else 54.0)
        db.upsert_activity({"id": f"b{index}", "date": day, "type": "basketball",
                            "duration_min": 90, "training_load": 150})
    keys = {finding["key"] for finding in discover_patterns(db)}
    assert "discovery.basketball_next_day_hrv" not in keys
```

Run to verify FAIL (`discover_patterns` missing). Note: dates span April — within the default 180-day window below.

- [ ] **Step 2: Implement detectors**

Append to `src/ai/discovery.py`:

```python
DISCOVERY_WINDOW_DAYS = 180
TRACKED_ACTIVITY_TYPES = ("basketball", "skiing", "hiking", "strength", "lap_swimming", "tennis_v2")


def _metrics_by_date(db: Database, days: int) -> dict[str, dict[str, Any]]:
    return {row["date"]: row for row in db.get_recent_metrics(days=days)}


def _next_day(day: str) -> str:
    from datetime import date as date_type, timedelta
    return str(date_type.fromisoformat(day) + timedelta(days=1))


def _activity_next_day_metric(
    db: Database, activity_type: str, metric: str, days: int
) -> dict[str, Any] | None:
    """Paired deltas: metric the morning after each session vs the morning of."""
    metrics = _metrics_by_date(db, days + 1)
    deltas: list[float] = []
    baselines: list[float] = []
    for activity in db.get_recent_activities(days=days, activity_type=activity_type):
        day = str(activity["date"])
        day_value = (metrics.get(day) or {}).get(metric)
        next_value = (metrics.get(_next_day(day)) or {}).get(metric)
        if day_value is None or next_value is None or day_value == 0:
            continue
        deltas.append(next_value - day_value)
        baselines.append(day_value)
    if not baselines:
        return None
    return gated_paired_effect(deltas, baseline_mean=sum(baselines) / len(baselines))


def _period_metric_shift(db: Database, metric: str, days: int) -> dict[str, Any] | None:
    """Two-sample: metric on active-period days vs all other days."""
    period_values: list[float] = []
    other_values: list[float] = []
    for row in db.get_recent_metrics(days=days):
        value = row.get(metric)
        if value is None:
            continue
        phase = str(row.get("menstrual_phase") or "").strip().lower()
        if phase in {"1", "period"}:
            period_values.append(value)
        else:
            other_values.append(value)
    return gated_two_sample_effect(period_values, other_values)


def _consecutive_day_readiness_cost(db: Database, days: int) -> dict[str, Any] | None:
    """Two-sample: readiness on days following a training day vs following rest."""
    metrics = _metrics_by_date(db, days + 1)
    activity_dates = {str(a["date"]) for a in db.get_recent_activities(days=days)}
    after_training: list[float] = []
    after_rest: list[float] = []
    for day, row in metrics.items():
        readiness = row.get("training_readiness_score")
        if readiness is None:
            continue
        from datetime import date as date_type, timedelta
        previous = str(date_type.fromisoformat(day) - timedelta(days=1))
        (after_training if previous in activity_dates else after_rest).append(readiness)
    return gated_two_sample_effect(after_training, after_rest)


def discover_patterns(db: Database, days: int = DISCOVERY_WINDOW_DAYS) -> list[dict[str, Any]]:
    """All gated discovery findings, ready for the insights store."""
    findings: list[dict[str, Any]] = []

    for activity_type in TRACKED_ACTIVITY_TYPES:
        effect = _activity_next_day_metric(db, activity_type, "hrv_last_night", days)
        if effect is not None:
            direction = "drops" if effect["mean_delta"] < 0 else "rises"
            findings.append({
                "key": f"discovery.{activity_type}_next_day_hrv",
                "statement": (
                    f"Your HRV {direction} {abs(effect['relative_effect']) * 100:.0f}% on average"
                    f" the morning after {activity_type} (n={effect['n']} sessions,"
                    f" mean {effect['mean_delta']:+.1f} ms, p={effect['p']})."
                ),
                "evidence": effect,
            })

    period_shift = _period_metric_shift(db, "resting_hr", days)
    if period_shift is not None:
        findings.append({
            "key": "discovery.period_resting_hr_shift",
            "statement": (
                f"Your resting HR runs {abs(period_shift['delta']):.1f} bpm"
                f" {'higher' if period_shift['delta'] > 0 else 'lower'} on active-period days"
                f" (n={period_shift['n_condition']} period days vs"
                f" {period_shift['n_comparison']} other days, p={period_shift['p']})."
            ),
            "evidence": period_shift,
        })

    consecutive = _consecutive_day_readiness_cost(db, days)
    if consecutive is not None:
        findings.append({
            "key": "discovery.consecutive_day_readiness_cost",
            "statement": (
                f"Mornings after a training day your readiness averages"
                f" {abs(consecutive['delta']):.0f} points"
                f" {'lower' if consecutive['delta'] < 0 else 'higher'} than after rest"
                f" (n={consecutive['n_condition']} vs {consecutive['n_comparison']} days,"
                f" p={consecutive['p']})."
            ),
            "evidence": consecutive,
        })

    return findings
```

- [ ] **Step 3: Run tests, full suite**

```bash
python -m pytest test/test_discovery_detectors.py -v && python -m pytest -q
```

Expected: PASS. If `test_activity_next_day_hrv_drop_detected` fails on the relative-effect gate: the planted drop is 12.5% vs the 5% floor, so a failure means a wiring bug, not a tuning issue — debug, don't loosen the gate.

- [ ] **Step 4: Commit**

```bash
git add src/ai/discovery.py test/test_discovery_detectors.py && git commit -m "Add discovery detectors: next-day HRV by activity, period RHR shift, consecutive-day readiness cost"
```

---

### Task 3: Evidence re-validation (numbers refresh as data grows)

**Files:**
- Modify: `src/db/models.py`, `src/ai/discovery.py`
- Test: `test/test_insight_refresh.py`

- [ ] **Step 1: Write failing test**

`test/test_insight_refresh.py`:

```python
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
```

Run to verify FAIL.

- [ ] **Step 2: Add `refresh_insight_evidence` to Database**

In `src/db/models.py`, after `mark_insight_adopted`:

```python
def refresh_insight_evidence(
    self, key: str, statement: str, evidence: dict[str, Any] | None
) -> bool:
    """Update statement/evidence for an existing insight, preserving its
    status and dates. Returns False when the key does not exist."""
    with self._connection() as conn:
        cursor = conn.execute(
            "UPDATE insights SET statement = ?, evidence_json = ? WHERE key = ?",
            (statement, json.dumps(evidence) if evidence is not None else None, key),
        )
        return cursor.rowcount > 0
```

- [ ] **Step 3: Add the store helper to discovery.py**

Append:

```python
def store_discovery_findings(db: Database) -> int:
    """Insert new findings; refresh evidence on existing keys. Returns count
    of NEW rows only."""
    inserted = 0
    for finding in discover_patterns(db):
        if db.insert_insight(
            key=finding["key"],
            category="discovery",
            statement=finding["statement"],
            evidence=finding["evidence"],
        ):
            inserted += 1
        else:
            db.refresh_insight_evidence(
                key=finding["key"],
                statement=finding["statement"],
                evidence=finding["evidence"],
            )
    return inserted
```

- [ ] **Step 4: Run suite, commit**

```bash
python -m pytest -q
git add src/db/models.py src/ai/discovery.py test/test_insight_refresh.py && git commit -m "Add evidence re-validation so insight numbers refresh as data grows"
```

---

### Task 4: Illness/overreach warning composite

**Files:**
- Create: `src/ai/warnings.py`
- Test: `test/test_warning_composite.py`

- [ ] **Step 1: Write failing tests**

`test/test_warning_composite.py`:

```python
from src.ai.warnings import health_warning


def _seed_baseline(db, days=28):
    for day_number in range(1, days + 1):
        db.upsert_daily_metrics({
            "date": f"2026-05-{day_number:02d}",
            "respiration_avg": 14.0,
            "resting_hr": 52.0,
            "hrv_last_night": 60.0,
            "sleep_score": 80.0,
        })


def test_fires_when_two_signals_deviate(db):
    _seed_baseline(db)
    db.upsert_daily_metrics({
        "date": "2026-06-10",
        "respiration_avg": 17.5,   # well above baseline
        "resting_hr": 58.0,        # well above baseline
        "hrv_last_night": 59.0,    # normal
        "sleep_score": 78.0,       # normal
    })
    warning = health_warning(db)
    assert warning is not None
    assert set(warning["fired_signals"]) == {"respiration_avg", "resting_hr"}


def test_silent_when_single_signal_deviates(db):
    _seed_baseline(db)
    db.upsert_daily_metrics({
        "date": "2026-06-10",
        "respiration_avg": 17.5,
        "resting_hr": 52.5,
        "hrv_last_night": 60.5,
        "sleep_score": 81.0,
    })
    assert health_warning(db) is None
```

Run to verify FAIL. Note: seeded baselines are constant ⇒ std=0; the implementation must guard zero-std (treat as no deviation is wrong here — use a minimum std floor instead, below).

- [ ] **Step 2: Implement**

`src/ai/warnings.py`:

```python
"""Multi-signal illness/overreach early warning.

Distinct from per-metric anomaly detection: this is a composite 'your body is
fighting something' judgment — ≥2 of 4 signals deviating adversely from the
personal 28-day baseline."""
from __future__ import annotations

from typing import Any

from ..db.models import Database

WARNING_BASELINE_DAYS = 28
WARNING_Z_THRESHOLD = 1.5
WARNING_MIN_SIGNALS = 2
WARNING_MIN_BASELINE_SAMPLES = 14
# metric -> adverse direction (+1: elevated is bad, -1: depressed is bad)
WARNING_SIGNALS = {
    "respiration_avg": 1,
    "resting_hr": 1,
    "hrv_last_night": -1,
    "sleep_score": -1,
}
# floors prevent zero/near-zero std from manufacturing infinite z-scores
MIN_STD = {"respiration_avg": 0.5, "resting_hr": 1.0, "hrv_last_night": 2.0, "sleep_score": 3.0}


def health_warning(db: Database) -> dict[str, Any] | None:
    """Today's composite warning, or None."""
    rows = db.get_recent_metrics(days=WARNING_BASELINE_DAYS + 1)
    if len(rows) < WARNING_MIN_BASELINE_SAMPLES + 1:
        return None
    rows_sorted = sorted(rows, key=lambda row: row["date"])
    today = rows_sorted[-1]
    history = rows_sorted[:-1]

    fired: list[str] = []
    details: dict[str, dict[str, float]] = {}
    for metric, adverse_direction in WARNING_SIGNALS.items():
        baseline_values = [r[metric] for r in history if r.get(metric) is not None]
        today_value = today.get(metric)
        if today_value is None or len(baseline_values) < WARNING_MIN_BASELINE_SAMPLES:
            continue
        mean = sum(baseline_values) / len(baseline_values)
        variance = sum((v - mean) ** 2 for v in baseline_values) / len(baseline_values)
        std = max(variance ** 0.5, MIN_STD[metric])
        z_score = (today_value - mean) / std
        details[metric] = {"value": today_value, "baseline": round(mean, 1), "z": round(z_score, 2)}
        if z_score * adverse_direction >= WARNING_Z_THRESHOLD:
            fired.append(metric)

    if len(fired) < WARNING_MIN_SIGNALS:
        return None
    return {"date": today["date"], "fired_signals": fired, "details": details}
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest test/test_warning_composite.py -v && python -m pytest -q
git add src/ai/warnings.py test/test_warning_composite.py && git commit -m "Add four-signal illness and overreach warning composite"
```

---

### Task 5: Instant warning channel through Riko

**Files:**
- Modify: `src/main.py`
- Test: `test/test_warning_channel.py` (file-write logic only; the OpenClaw cron is created at deploy)

- [ ] **Step 1: Write failing test**

`test/test_warning_channel.py`:

```python
from src.main import _write_health_alert


def test_alert_file_and_cooldown(db, tmp_path):
    warning = {
        "date": "2026-06-11",
        "fired_signals": ["respiration_avg", "resting_hr"],
        "details": {
            "respiration_avg": {"value": 17.5, "baseline": 14.0, "z": 2.4},
            "resting_hr": {"value": 58.0, "baseline": 52.0, "z": 2.1},
            "hrv_last_night": {"value": 59.0, "baseline": 60.0, "z": -0.3},
            "sleep_score": {"value": 78.0, "baseline": 80.0, "z": -0.4},
        },
    }
    alert_path = tmp_path / "health-alert.txt"

    wrote = _write_health_alert(db, warning, alert_path)

    assert wrote is True
    content = alert_path.read_text()
    assert "respiration_avg" in content and "17.5" in content
    # Cooldown recorded ⇒ second warning inside 48h is suppressed.
    assert _write_health_alert(db, warning, alert_path) is False
```

Run to verify FAIL.

- [ ] **Step 2: Implement the writer + trigger + cmd_sync hook**

In `src/main.py`, next to `_write_weekly_insight_card`:

```python
HEALTH_ALERT_COOLDOWN_HOURS = 48
DEFAULT_RIKO_HEALTH_ALERT_CRON_NAME = "Riko Health Alert"


def _write_health_alert(db, warning: dict, alert_path: Path | None = None) -> bool:
    """Persist the computed warning for Riko and record the cooldown.
    Returns True when a fresh alert was written."""
    if db.hours_since_last_notification("health_warning") < HEALTH_ALERT_COOLDOWN_HOURS:
        return False
    target = alert_path or (Path.home() / "ai" / "data" / "signals" / "health-alert.txt")
    lines = [f"# Health Warning — {warning['date']}", ""]
    lines.append(f"Fired signals (adverse, ≥1.5σ vs 28d baseline): {', '.join(warning['fired_signals'])}")
    for metric, info in warning["details"].items():
        marker = "  <-- FIRED" if metric in warning["fired_signals"] else ""
        lines.append(f"- {metric}: {info['value']} (baseline {info['baseline']}, z={info['z']}){marker}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n")
    db.add_notification("health_warning", ",".join(warning["fired_signals"]))
    return True


def _trigger_riko_health_alert() -> bool:
    """Trigger the disabled 'Riko Health Alert' OpenClaw cron, mirroring
    _trigger_riko_analysis. Cron id: env override, else lookup by name."""
    import shutil
    import subprocess
    cron_path = os.pathsep.join([
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
        os.environ.get("PATH", ""),
    ])
    openclaw_bin = shutil.which("openclaw", path=cron_path) or "/opt/homebrew/bin/openclaw"
    cron_id = os.environ.get("RIKO_HEALTH_ALERT_CRON_ID") or _openclaw_cron_id_by_name(
        openclaw_bin, cron_path, DEFAULT_RIKO_HEALTH_ALERT_CRON_NAME
    )
    if cron_id is None:
        print("WARNING: Riko Health Alert cron not found; alert file written but not pushed")
        return False
    try:
        result = subprocess.run(
            [openclaw_bin, "cron", "run", cron_id],
            env={**os.environ, "PATH": cron_path},
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"Riko health alert triggered (cron {cron_id})")
            return True
        print(f"WARNING: health alert trigger failed: {result.stderr[:200]}")
    except Exception as error:
        print(f"WARNING: health alert trigger failed: {error}")
    return False
```

In `cmd_sync`, directly after the strength-findings block (before the Saturday card):

```python
    # Illness/overreach composite — instant channel, 48h cooldown.
    from .ai.warnings import health_warning
    try:
        warning = health_warning(sync.db)
        if warning is not None and _write_health_alert(sync.db, warning):
            _trigger_riko_health_alert()
    except Exception as error:
        logger.warning("Health warning check failed: %s", error)

    # Discovery detectors — same cheap-and-idempotent contract.
    from .ai.discovery import store_discovery_findings
    try:
        new_discoveries = store_discovery_findings(sync.db)
        if new_discoveries:
            print(f"New discovery findings stored: {new_discoveries}")
    except Exception as error:
        logger.warning("Discovery detection failed: %s", error)
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest test/test_warning_channel.py -v && python -m pytest -q
git add src/main.py test/test_warning_channel.py && git commit -m "Wire health warning composite and discovery miner into cmd_sync

Warnings write a computed alert file and fire the disabled Riko Health Alert cron via openclaw cron run, with a 48h cooldown through the notifications table. Discovery findings insert-or-refresh into the insights store."
```

---

### Task 6: Basketball detectors

**Files:**
- Modify: `src/garmin/fit_parser.py` (HR series), create `src/ai/basketball_profile.py`
- Modify: `src/main.py` (CLI)
- Test: `test/test_basketball_profile.py`

- [ ] **Step 1: Add a generic HR-series parser to fit_parser.py**

Following the existing fitdecode frame-iteration style in `parse_gym_session`:

```python
def parse_hr_series(fit_path: str | Path) -> list[tuple[float, int]]:
    """(elapsed_seconds, heart_rate) for every record frame with HR."""
    series: list[tuple[float, int]] = []
    start_time = None
    with fitdecode.FitReader(str(fit_path)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                continue
            timestamp = _get_field(frame, "timestamp")
            heart_rate = _get_field(frame, "heart_rate")
            if timestamp is None or heart_rate is None:
                continue
            if start_time is None:
                start_time = timestamp
            series.append(((timestamp - start_time).total_seconds(), int(heart_rate)))
    return series
```

- [ ] **Step 2: Write failing tests for the analyzers (pure functions, no FIT files needed)**

`test/test_basketball_profile.py`:

```python
from src.ai.basketball_profile import hr_drift_pct, zone45_share

from test.conftest import seed_strength_activity  # noqa: F401  (db fixture import side)


def test_hr_drift_detects_second_half_rise():
    # 40 minutes: first half avg ~150, second half avg ~165 ⇒ +10%.
    series = [(t * 60.0, 150) for t in range(20)] + [(1200 + t * 60.0, 165) for t in range(20)]
    assert 9.0 <= hr_drift_pct(series) <= 11.0


def test_hr_drift_requires_minimum_duration():
    series = [(t * 60.0, 150) for t in range(10)]  # 10 minutes only
    assert hr_drift_pct(series) is None


def test_zone45_share(db):
    db.upsert_activity({
        "id": "bb1", "date": "2026-06-01", "type": "basketball", "duration_min": 60,
        "hr_zone1_sec": 600, "hr_zone2_sec": 900, "hr_zone3_sec": 900,
        "hr_zone4_sec": 900, "hr_zone5_sec": 300,
    })
    shares = zone45_share(db, days=90)
    assert shares == [{"date": "2026-06-01", "share": 0.33}]
```

Run to verify FAIL.

- [ ] **Step 3: Implement `src/ai/basketball_profile.py`**

```python
"""Basketball-specific conditioning analysis: in-session HR drift, high-zone
share trend, and the day-after cost (the latter comes from discovery.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db.models import Database

DRIFT_MIN_DURATION_SEC = 20 * 60
DRIFT_TRIM_FRACTION = 0.1  # ignore first/last 10% (warm-up, cooldown)


def hr_drift_pct(series: list[tuple[float, int]]) -> float | None:
    """Second-half vs first-half mean HR, % — a conditioning-fade proxy."""
    if not series or series[-1][0] < DRIFT_MIN_DURATION_SEC:
        return None
    total = series[-1][0]
    trimmed = [(t, hr) for t, hr in series
               if DRIFT_TRIM_FRACTION * total <= t <= (1 - DRIFT_TRIM_FRACTION) * total]
    if len(trimmed) < 10:
        return None
    midpoint = (trimmed[0][0] + trimmed[-1][0]) / 2
    first = [hr for t, hr in trimmed if t <= midpoint]
    second = [hr for t, hr in trimmed if t > midpoint]
    if not first or not second:
        return None
    first_mean = sum(first) / len(first)
    if first_mean == 0:
        return None
    return round(100 * (sum(second) / len(second) - first_mean) / first_mean, 1)


def zone45_share(db: Database, days: int = 90) -> list[dict[str, Any]]:
    """Per-session share of time in HR zones 4-5, date ascending."""
    shares: list[dict[str, Any]] = []
    for activity in db.get_recent_activities(days=days, activity_type="basketball"):
        zone_seconds = [activity.get(f"hr_zone{zone}_sec") or 0 for zone in range(1, 6)]
        total = sum(zone_seconds)
        if total == 0:
            continue
        shares.append({
            "date": str(activity["date"]),
            "share": round((zone_seconds[3] + zone_seconds[4]) / total, 2),
        })
    return sorted(shares, key=lambda row: row["date"])


def basketball_profile_block(db: Database, days: int = 90) -> str:
    """Formatted block — same register as the strength profile."""
    from ..garmin.fit_parser import parse_hr_series

    lines = ["## Basketball Profile (computed — LLM MUST use this)"]
    drifts: list[tuple[str, float]] = []
    for activity in db.get_recent_activities(days=days, activity_type="basketball"):
        fit_path = activity.get("fit_file_path")
        if not fit_path or not Path(fit_path).exists():
            continue
        try:
            drift = hr_drift_pct(parse_hr_series(fit_path))
        except Exception:
            continue
        if drift is not None:
            drifts.append((str(activity["date"]), drift))
    if drifts:
        drifts.sort()
        recent = ", ".join(f"{day}: {value:+.1f}%" for day, value in drifts[-5:])
        lines.append(f"HR drift (2nd half vs 1st, last {min(len(drifts), 5)} sessions): {recent}")

    shares = zone45_share(db, days=days)
    if shares:
        recent_shares = ", ".join(f"{row['date']}: {row['share']:.0%}" for row in shares[-5:])
        lines.append(f"Zone 4-5 share per session: {recent_shares}")

    if len(lines) == 1:
        lines.append("No basketball sessions with usable HR data in the window.")
    return "\n".join(lines)
```

- [ ] **Step 4: Add the CLI subcommand**

In `src/main.py` — mirror `cmd_strength_profile` exactly (direct `load_config` + `Database`, no `build_components`):

```python
def cmd_basketball_profile(args: argparse.Namespace) -> None:
    """Print the computed basketball profile. Consumed by Riko for Q&A."""
    config = load_config(args.config)
    db = Database(config.data_dir / "garmin.db")
    from .ai.basketball_profile import basketball_profile_block
    print(basketball_profile_block(db, days=args.days))
```

Register: parser `basketball-profile` with `--days` (default 90) in the subparser block, and `"basketball-profile": cmd_basketball_profile` in the commands dict.

- [ ] **Step 5: Run tests, then a live read against a prod copy**

```bash
python -m pytest -q
scp mini:projects/garmin-ai-coach/data/garmin.db /tmp/garmin-p2.db
# fit files live on mini; HR drift lines may be empty locally — that's expected.
# Full verification happens on mini at deploy.
mkdir -p /tmp/p2-data && cp /tmp/garmin-p2.db /tmp/p2-data/garmin.db
printf 'garmin:\n  email: "d@e.f"\n  password: "d"\ntelegram:\n  bot_token: "d"\n  chat_id: "0"\nllm:\n  api_key: "d"\n  model: "gpt-4o"\ndata_dir: /tmp/p2-data\n' > /tmp/p2-config.yaml
python -m src.main --config /tmp/p2-config.yaml basketball-profile
```

Expected: zone 4-5 share lines from real sessions; drift lines likely absent locally (fit paths are mini-local).

- [ ] **Step 6: Commit**

```bash
git add src/garmin/fit_parser.py src/ai/basketball_profile.py src/main.py test/test_basketball_profile.py && git commit -m "Add basketball conditioning detectors: HR drift, zone 4-5 share, profile CLI"
```

---

### Task 7: Monthly progression narrative

**Files:**
- Modify: `src/main.py`
- Test: `test/test_monthly_narrative.py`

- [ ] **Step 1: Write failing test**

`test/test_monthly_narrative.py`:

```python
from src.main import _write_monthly_narrative


def test_writes_once_per_month(db, tmp_path):
    db.upsert_daily_metrics({"date": "2026-06-10", "vo2max_running": 38.0,
                             "endurance_score": 5400, "hrv_last_night": 60.0})
    target = tmp_path / "monthly-narrative.txt"

    assert _write_monthly_narrative(db, target) is True
    assert target.exists()
    # Second call within the month: suppressed via notifications cooldown.
    assert _write_monthly_narrative(db, target) is False
```

Run to verify FAIL.

- [ ] **Step 2: Implement**

In `src/main.py`:

```python
MONTHLY_NARRATIVE_COOLDOWN_HOURS = 21 * 24  # >2 weeks ⇒ once per month in practice


def _write_monthly_narrative(db, target_path: Path | None = None) -> bool:
    """Compose the monthly progression narrative inputs from the computed
    user model. Pure data file — Riko writes the prose."""
    if db.hours_since_last_notification("monthly_narrative") < MONTHLY_NARRATIVE_COOLDOWN_HOURS:
        return False
    from .ai.user_model import build_user_model
    target = target_path or (Path.home() / "ai" / "data" / "signals" / "monthly-narrative.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_user_model(db))
    db.add_notification("monthly_narrative", str(date.today()))
    return True
```

Note: `build_user_model` already contains `_progression_trajectory` and `_blind_spots`; reuse it wholesale rather than re-extracting pieces (check its import path in `cmd_whoami`). `date` — module-level import check: `cmd_sync` imports it function-locally; `_write_monthly_narrative` needs its own `from datetime import date` if `date` is not module-level in main.py.

In `cmd_sync`, inside the existing Saturday block:

```python
    if date.today().weekday() == 5:
        try:
            if _write_weekly_insight_card(sync.db):
                print("Weekly insight card written")
        except Exception as error:
            logger.warning("Insight card failed: %s", error)
        if date.today().day <= 7:  # first Saturday of the month
            try:
                if _write_monthly_narrative(sync.db):
                    print("Monthly narrative written")
            except Exception as error:
                logger.warning("Monthly narrative failed: %s", error)
```

- [ ] **Step 3: Run tests, commit**

```bash
python -m pytest -q
git add src/main.py test/test_monthly_narrative.py && git commit -m "Write monthly progression narrative inputs on the first Saturday"
```

---

### Task 8: Adoption feedback loop + insight CLI

**Files:**
- Modify: `src/main.py` (CLI), `src/ai/insights.py` (`_estimated_recovery_hours` call path)
- Create: `src/ai/adopted.py`
- Test: `test/test_adopted_modifiers.py`

- [ ] **Step 1: Write failing test**

`test/test_adopted_modifiers.py`:

```python
from src.ai.adopted import recovery_modifiers


def test_adopted_hrv_insight_becomes_recovery_multiplier(db):
    db.insert_insight(
        key="discovery.basketball_next_day_hrv",
        category="discovery",
        statement="HRV drops 22% after basketball",
        evidence={"n": 10, "relative_effect": -0.22, "mean_delta": -13.0, "p": 0.01},
    )
    row = db.get_insights()[0]
    db.mark_insight_adopted(row["id"], rule_ref="recovery_modifier")

    modifiers = recovery_modifiers(db)

    assert modifiers["basketball"] > 1.0          # recovers worse ⇒ longer recovery
    assert modifiers["basketball"] <= 1.5         # capped


def test_non_adopted_insights_have_no_effect(db):
    db.insert_insight(
        key="discovery.skiing_next_day_hrv",
        category="discovery",
        statement="s", evidence={"relative_effect": -0.10},
    )
    assert "skiing" not in recovery_modifiers(db)
```

Run to verify FAIL.

- [ ] **Step 2: Implement `src/ai/adopted.py`**

```python
"""Adopted insights become coach-layer rules. Only status='adopted' rows have
behavioral effect — Bodhi (via Riko) approves each one explicitly."""
from __future__ import annotations

import json
import re

from ..db.models import Database

RECOVERY_MODIFIER_CAP = 1.5
RECOVERY_MODIFIER_FLOOR = 0.8
ADOPTED_KEY_PATTERN = re.compile(r"^discovery\.([a-z_0-9]+)_next_day_hrv$")


def recovery_modifiers(db: Database) -> dict[str, float]:
    """activity_type -> recovery-time multiplier, from adopted next-day-HRV
    insights. A -22% HRV hit maps to a 1.22x recovery multiplier, capped."""
    modifiers: dict[str, float] = {}
    for row in db.get_insights(status="adopted"):
        match = ADOPTED_KEY_PATTERN.match(row["key"])
        if match is None or not row["evidence_json"]:
            continue
        evidence = json.loads(row["evidence_json"])
        relative_effect = evidence.get("relative_effect")
        if relative_effect is None:
            continue
        multiplier = 1.0 - relative_effect  # -0.22 ⇒ 1.22; +0.09 ⇒ 0.91
        modifiers[match.group(1)] = round(
            min(max(multiplier, RECOVERY_MODIFIER_FLOOR), RECOVERY_MODIFIER_CAP), 2
        )
    return modifiers
```

- [ ] **Step 3: Wire into the recovery estimate**

Find every call site of `_estimated_recovery_hours` in `src/ai/insights.py` (`grep -n "_estimated_recovery_hours" src/ai/insights.py`). At the call path that has access to both `db` and the activity, scale the estimate:

```python
    from .adopted import recovery_modifiers
    modifier = recovery_modifiers(db).get(str(activity.get("type")), 1.0)
    recovery_hours = round(_estimated_recovery_hours(training_load, anaerobic_te, intent) * modifier)
```

Read the surrounding function first and keep its existing variable names; if multiple call sites exist, apply the modifier in all of them and state in the commit message which functions changed. Add a line to the output near the estimate when `modifier != 1.0`, e.g. `f" (personalized x{modifier} from adopted insight)"` — the Coach Contract requires the override to be visible, not silent.

- [ ] **Step 4: Add the `insight` CLI**

In `src/main.py` (direct `load_config` + `Database`, like `cmd_strength_profile`):

```python
def cmd_insight(args: argparse.Namespace) -> None:
    """List/adopt/dismiss insights. Riko calls adopt/dismiss on Bodhi's word."""
    config = load_config(args.config)
    db = Database(config.data_dir / "garmin.db")
    if args.action == "list":
        for row in db.get_insights(status=args.status):
            print(f"[{row['status']:>9}] {row['key']}: {row['statement']}")
        return
    matches = [row for row in db.get_insights() if row["key"] == args.key]
    if not matches:
        print(f"No insight with key {args.key}")
        return
    if args.action == "adopt":
        db.mark_insight_adopted(matches[0]["id"], rule_ref=args.rule or "manual")
        print(f"Adopted: {args.key}")
    elif args.action == "dismiss":
        with db._connection() as conn:
            conn.execute("UPDATE insights SET status = 'dismissed' WHERE id = ?", (matches[0]["id"],))
        print(f"Dismissed: {args.key}")
```

Wait — don't reach into `db._connection` from main.py; add a proper `mark_insight_dismissed(insight_id)` method to `Database` (same shape as `mark_insight_surfaced`, sets status only) and call that instead.

Register the subparser:

```python
    insight_parser = subparsers.add_parser("insight", help="List/adopt/dismiss insights")
    insight_parser.add_argument("action", choices=["list", "adopt", "dismiss"])
    insight_parser.add_argument("key", nargs="?", default=None, help="Insight key (adopt/dismiss)")
    insight_parser.add_argument("--status", default=None, help="Filter for list")
    insight_parser.add_argument("--rule", default=None, help="Rule reference (adopt)")
```

And `"insight": cmd_insight` in the commands dict.

- [ ] **Step 5: Run suite, commit**

```bash
python -m pytest -q
git add src/ai/adopted.py src/ai/insights.py src/db/models.py src/main.py test/test_adopted_modifiers.py && git commit -m "Turn adopted insights into personalized recovery modifiers with an insight CLI

recovery_modifiers maps adopted next-day-HRV findings to capped recovery-time multipliers; the recovery estimate path applies and displays them. The insight CLI gives Riko adopt/dismiss verbs so Bodhi approves which discoveries become rules."
```

---

### Task 9: Coach Contract prompt wiring (Riko side)

**Files:** none in repo — OpenClaw cron prompts on mini, patched with the scp'd-script pattern.

- [ ] **Step 1: Compose the contract block (identical for all three prompts)**

```
=== COACH CONTRACT ===
Authority order: hard constraints > professional judgment > Bodhi's preferences > generic advice.
- Hard constraints are never violated (active period ⇒ no swim; pain ≥4/10 or RPE ≥8 ⇒ deload next session).
- Professional judgment MAY override a preference, but never silently: name the preference, cite the data (from computed layers — never invent numbers), state the call, then give the preference-compatible variant.
- Preferences shape HOW, not WHETHER: a structural need is addressed through variants she accepts, not dropped.
- Every number you present must come from a computed layer or file; population norms must be labeled as such.
```

- [ ] **Step 2: Patch all three cron prompts**

For each of: Training Push (`bff6527a-9d3c-4b1b-acac-8f06a63fa1dc`), Training Deep Review (`029761c8-aef8-4820-8653-bdc6d9be4ab6`), and Riko Health Alert (created in Task 10):

```bash
ssh mini "openclaw cron show <ID> --json | jq -r '.payload.message' > /tmp/cron-prompt.txt"
# patch script inserts the contract block after the first paragraph; assert not already present
scp /tmp/patch_contract.py mini:/tmp/ && ssh mini 'python3 /tmp/patch_contract.py /tmp/cron-prompt.txt'
ssh mini 'openclaw cron edit <ID> --message "$(cat /tmp/cron-prompt.txt)"'
```

`/tmp/patch_contract.py` (written locally, scp'd — never heredoc'd through zsh):

```python
import sys
from pathlib import Path

CONTRACT = """\n=== COACH CONTRACT ===\nAuthority order: hard constraints > professional judgment > Bodhi's preferences > generic advice.\n- Hard constraints are never violated (active period => no swim; pain >=4/10 or RPE >=8 => deload next session).\n- Professional judgment MAY override a preference, but never silently: name the preference, cite the data (from computed layers - never invent numbers), state the call, then give the preference-compatible variant.\n- Preferences shape HOW, not WHETHER: a structural need is addressed through variants she accepts, not dropped.\n- Every number you present must come from a computed layer or file; population norms must be labeled as such.\n"""

p = Path(sys.argv[1])
text = p.read_text()
assert "COACH CONTRACT" not in text, "already patched"
first_break = text.index("\n\n")
p.write_text(text[:first_break] + "\n" + CONTRACT + text[first_break:])
print("patched", p, len(text), "->", p.stat().st_size)
```

- [ ] **Step 3: Verify no drift on every patched cron**

```bash
ssh mini 'for id in bff6527a-9d3c-4b1b-acac-8f06a63fa1dc 029761c8-aef8-4820-8653-bdc6d9be4ab6 <HEALTH_ALERT_ID>; do openclaw cron show $id --json | jq "{id: .id[0:8], enabled, schedule: .schedule, hasContract: (.payload.message | contains(\"COACH CONTRACT\"))}"; done'
```

Expected: Training Push still `enabled: false`; Deep Review still `enabled: true` with `0 9 * * 6`; all `hasContract: true`. **The Training Push enabled-state is the 2026-04-24 incident invariant — verify it explicitly.**

---

### Task 10: Backtest, review gate, deploy

**Files:**
- Modify: `scripts/backtest_insights.py`

- [ ] **Step 1: Extend the backtest script**

Append to `scripts/backtest_insights.py`:

```python
print("\n=== Discovery findings (gated) ===")
from src.ai.discovery import discover_patterns
for finding in discover_patterns(db):
    print(f"- [{finding['key']}] {finding['statement']}")
    print(f"  evidence: {finding['evidence']}")

print("\n=== Warning composite — would it fire today? ===")
from src.ai.warnings import health_warning
warning = health_warning(db)
print(warning if warning else "No warning (composite below threshold)")

print("\n=== Basketball profile ===")
from src.ai.basketball_profile import basketball_profile_block
print(basketball_profile_block(db))
```

- [ ] **Step 2: Run against a fresh prod copy and STOP for Bodhi's review**

```bash
scp mini:projects/garmin-ai-coach/data/garmin.db /tmp/garmin-p2-backtest.db && python scripts/backtest_insights.py /tmp/garmin-p2-backtest.db
```

**STOP. Show the full output to Bodhi** — every discovery statement, whether the warning would fire today, the basketball numbers. Tune statements/gates per her feedback before deploying. Commit the script: `git add scripts/backtest_insights.py && git commit -m "Extend backtest with discovery, warning, and basketball output"`.

- [ ] **Step 3: Merge, push, pull on mini, test there**

```bash
cd ~/projects/garmin-ai-coach && git checkout main && git merge insight_pipeline_phase2 --no-edit && git push origin main
ssh mini 'cd ~/projects/garmin-ai-coach && git pull --ff-only && .venv/bin/pip install -e ".[dev]" -q; .venv/bin/python -m pytest -q'
```

- [ ] **Step 4: Create the Riko Health Alert cron (disabled, externally triggered)**

```bash
ssh mini 'openclaw cron add --name "Riko Health Alert" --cron "0 0 31 2 *" --disabled --session isolated --announce --channel telegram --message "Read ~/ai/data/signals/health-alert.txt (computed multi-signal health warning). Write a short Telegram alert: first line = what to change about TODAY'\''s training (recommendation-first), then ONE line listing the fired signals with their values vs baseline. Max 4 lines, no headers, no schema labels. Respect the COACH CONTRACT block if present in your context."'
```

Check `openclaw cron add --help` first for exact flag names (`--disabled` vs `--no-enable`, channel syntax) — mirror how Training Push is configured (`openclaw cron show bff6527a... --json`). Set `lightContext` the same way the Training Push has it. Then verify:

```bash
ssh mini 'openclaw cron show <NEW_ID> --json | jq "{enabled, lightContext: .payload.lightContext // .lightContext, announce: .deliver // .announce}"'
```

Expected: `enabled: false`. Record the new cron id in `~/projects/garmin-ai-coach/README.md`'s operations section AND in the memory file `project_garmin_insight_pipeline.md`.

- [ ] **Step 5: Manual sync + end-to-end verify on mini**

```bash
ssh mini '/Users/mini/scripts/garmin-run.sh -m src.main sync 2>&1 | tail -6'
ssh mini 'sqlite3 ~/projects/garmin-ai-coach/data/garmin.db "SELECT key, status FROM insights ORDER BY id" | tail -8'
ssh mini 'cd ~/projects/garmin-ai-coach && .venv/bin/python -m src.main basketball-profile | head -6'
```

Expected: sync clean, discovery rows present (insert-or-refreshed), basketball profile shows real HR drift values (fit files exist on mini). Verify the morning push pipeline stays healthy the next morning (`training-pushed-*` flag appears).

- [ ] **Step 6: Run Task 9 (contract wiring) now that the Health Alert cron exists, then close the loop**

After Task 9's verification passes: update `project_garmin_insight_pipeline.md` (Phase 2 live, health-alert cron id, anything learned), and tell Bodhi what will happen organically next: the next health deviation triggers an instant alert; Saturday brings the next insight card; the first Saturday of July brings the first monthly narrative; "adopt"-ing a card via Riko changes her recovery prescriptions.

---

## Self-review notes

- **Spec coverage:** §4.2 → Tasks 1-3; §4.3 → Tasks 4-5; §4.5 → Task 6; §6-monthly → Task 7; §7 → Task 8; §2 wiring → Task 9; §9 backtest gate → Task 10 Step 2. Phase 2 closes the spec.
- **Ordering note:** Task 9 depends on Task 10 Step 4 (cron must exist) — execute 9 after 10 Step 4, as Task 10 Step 6 directs.
- **Known unknowns flagged inline:** `openclaw cron add` exact flags (Task 10 Step 4 — inspect help + mirror Training Push), `_estimated_recovery_hours` call-site shape (Task 8 Step 3 — read before editing), `build_user_model` import path (Task 7), module-level `date` in main.py (Task 7).
- **Type consistency:** `gated_paired_effect(deltas, baseline_mean) -> dict|None` and `gated_two_sample_effect(a, b) -> dict|None` consumed in Task 2; `refresh_insight_evidence(key, statement, evidence) -> bool` defined Task 3 and used in `store_discovery_findings`; `health_warning(db) -> dict|None` consumed by `_write_health_alert(db, warning, path) -> bool` in Task 5; `recovery_modifiers(db) -> dict[str, float]` consumed in Task 8 Step 3.
- **Determinism:** permutation tests seeded (`PERMUTATION_SEED=7`); no wall-clock randomness.
