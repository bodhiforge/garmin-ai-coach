# Phase 3: Watch Loop, Sleep Rhythm Coach, Deload Planning — Design

Date: 2026-06-11
Status: approved decisions baked in (daily watch push / auto-apply deload with notice / sleep insights passive-only)

## 1. Vision

Three extensions on the live insight pipeline, all honoring the Coach Contract and the passive-data principle:

- **F1 Watch loop** — the daily plan physically lands on the Forerunner 955 every morning, readiness-adjusted; completion data flows back into progression. The coaching loop reaches the wrist.
- **F2 Sleep rhythm coach** — circadian pattern discovery (bedtime variance, the quantified cost of late nights, personal optimal sleep window) surfaced through existing weekly cards and monthly narrative. No new pushes, no reminders.
- **F3 Deload planning** — the system detects accumulating fatigue at week granularity and **auto-applies** a deload week to the next weekly program, announcing the decision with evidence in the next morning push. Bodhi can veto via Riko in one sentence.

## 2. F1 — Daily watch push (morning loop)

**Decision: push the day's workout every morning** (not weekly batches), riding the existing Riko Training Push.

### Flow

```
cmd_sync detects wake → digest (existing)
→ Riko Training Push generates today's decision (existing)
→ IF decision A is a gym/strength session:
    Riko builds the structured plan JSON (existing workout_structured.md schema,
    from Exercise Progression + Weekly Programming layers — never invented numbers)
    → runs `python -m src.main push-workout '<json>'` (existing CLI)
    → NEW: the command also schedules the workout on today's date
→ report delivered (existing) — the push mentions "已推到手表" only when upload succeeded
```

### Changes

- `src/garmin/workout.py`: add `schedule_workout(client, workout_id, target_date)` calling the garminconnect calendar-schedule endpoint (verify the installed lib's method name at implementation; fall back to raw connectapi call if absent).
- `cmd_push_workout`: after a successful upload, schedule for today (or `--date`); record in the existing workout tracker for same-day dedup (no double-push when Phase 3 of the sync state machine retries).
- Riko Training Push cron prompt: add the conditional instruction (strength-day → build JSON → push → confirm in the report line). Prompt edit via the scp'd-script pattern, with enabled/schedule drift verification.
- Non-strength days: nothing is pushed; basketball/outdoor days remain advice-only.

### Failure containment

Watch push is best-effort: upload/schedule failures must not block report delivery — the report line degrades to "手表推送失败,照 plan 练" with the plan inline.

## 3. F2 — Sleep rhythm coach (passive only)

**Decision: findings surface through Saturday card + monthly narrative only.** No bedtime reminders, no new push cadence.

### Detectors (discovery.py, same statistical gates)

1. **Late-night cost** (paired/two-sample): nights with `sleep_start` ≥60 min later than the personal 60-day median vs normal nights → deltas in `sleep_deep_min`, `sleep_score`, next-day `hrv_last_night`. Gated finding e.g. "晚睡 ≥1h 的夜晚深睡平均少 X 分钟 (n=…, p=…)".
2. **Bedtime consistency** (descriptive + threshold): rolling 28-day std of `sleep_start`; fires when std > 75 min — "你的入睡时间波动 ±X 分钟,一致性是睡眠质量的第一杠杆".
3. **Personal optimal window** (descriptive, monthly narrative only): sleep_start half-hour buckets vs mean sleep_score/deep, minimum n per bucket = 5; reported as "你的最佳入睡窗口看起来是 HH:MM–HH:MM" with bucket counts.

All read existing `daily_metrics` columns (`sleep_start`, `sleep_deep_min`, `sleep_score`); sleep_start needs midnight-crossing normalization (e.g. 23:30 vs 00:45 — map to minutes relative to 18:00).

### Surfacing

Findings 1-2 → insights store → Saturday card queue (existing FIFO). Finding 3 → a "Sleep rhythm" section appended to the monthly narrative inputs.

## 4. F3 — Deload week planning

**Decision: auto-apply + announce with evidence; veto via Riko.**

### Detection (`src/ai/deload.py`, pure Python)

Fire when ALL hold:
- chronic load (28d) trending up ≥3 consecutive weeks (week-over-week sums of corrected load), AND
- recovery markers degrading: 7d HRV mean ≤ 95% of 28d mean, OR 7d readiness mean ≤ 28d mean − 10 points, AND
- no deload in the last 28 days (`notifications` type `deload`, cooldown).

Output: evidence dict (weekly load series, HRV/readiness ratios) — same register as discovery findings.

### Application (no new push channel; Riko stays the single outlet)

1. cmd_sync (daily, post-detectors) runs the check; on fire it writes `~/ai/data/signals/deload-directive.txt` (machine-readable: target week, volume cut 40-50%, keep movement-quality work, evidence) and records the notification.
2. **Next morning's Training Push announces it** — digest builder includes a `DELOAD` line when the directive is fresh; the push leads with the decision + evidence per Coach Contract ("负荷连涨 3 周 + HRV 基线 -7%,下周降量 45%。不同意就说一声。").
3. **Saturday Deep Review applies it** — prompt instructed: a fresh directive (<8 days) shapes `weekly-plan.md` (volume cut, no progression jumps, keep home micro-sessions). Veto path: Bodhi tells Riko → Riko deletes the directive file → Deep Review plans normally.
4. Insight store row `deload.applied_YYYY_WW` (category `deload`) for the audit trail.

## 5. Out of scope

- Bedtime reminders / any new daily push (F2 decision).
- Weekly batch watch-push or two-pipeline scheduling (F1 decision).
- Nutrition/body-comp/anything requiring manual logging.

## 6. Testing

Same Phase 1/2 contract: TDD per calculator with seeded fixtures; sleep_start normalization unit-tested across midnight; deload rule tested on planted load/HRV series (fires, respects cooldown, silent on healthy data); watch push verified on mini with a real upload to a throwaway workout (then deleted), schedule call verified against Garmin Connect calendar; backtest gate before deploy as always.
