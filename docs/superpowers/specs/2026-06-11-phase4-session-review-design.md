# Phase 4: Post-Session Review — Design

Date: 2026-06-11
Status: decisions baked in (content = data audit + next-step + subjective prompt; scope = all real training types)

## 1. Vision

Every real training session gets a proactive 复盘 push within ~30 minutes of syncing: what happened vs plan, what the data says, what changes next time, and one gentle question to capture how it felt. This closes the per-session loop the way the morning push closes the per-day loop — and it absorbs the existing Training Follow-Up so there is never more than one post-session message.

## 2. Trigger chain (no new cron schedule — same externally-triggered pattern)

```
cmd_sync (*/30, existing) → recent activities (last 3 days)
→ filter: reviewable type (exclude walking) AND duration ≥ 15 min
→ NOT yet reviewed (notification `session_review_{activity_id}` absent)
→ DUE: now ≥ activity_end + buffer(type)
→ Python writes ~/ai/data/signals/session-review.txt (computed blocks, zero LLM)
→ mark dedup notifications → trigger "Riko Session Review" cron (managed prompt,
  disabled, `openclaw cron run` by name) → Riko writes the Telegram review
```

**Correction buffer (Bodhi edits Garmin data post-hoc, typically within 1-2h):**
- **strength: 2 hours after activity end** — sets/weights/exercise names are exactly what she corrects; `refresh_recent_gym_sets` (runs every sync) has pulled her edits by then.
- **all other types: no buffer** (next sync, ~30 min) — HR/zones/runs are not manually edited.
- Due-ness is recomputed from the DB each sync; no extra state files. A not-yet-due session simply waits for a later sync cycle.

Multiple due activities in one sync (rare): the signal file contains one block per pending activity; one trigger, one combined push.

## 3. Computed content (`src/ai/session_review.py`, pure Python)

**Common (all types):** type, duration, corrected load (basketball correction applied), avg/max HR, zone distribution, and comparison vs her recent typical session of the same type (load and duration vs 90-day mean).

**Per-type depth:**
- **strength:** per-exercise sets (reps×weight), e1RM vs previous best — **PR detection gets celebrated**, volume per muscle group, rest median for the session, progression audit (did the session follow the progression layer's last prescription).
- **basketball:** in-session HR drift (FIT now downloaded), zone 4-5 share vs her trend, corrected load.
- **skiing:** run count, speed vs season average, which run speed faded (existing ski insights).
- **hiking/other:** load, elevation gain, duration vs typical.

**Next-step line:** computed from the existing progression/feedback-loop rules — concrete ("RDL 下次 +2.5lb" / "保持重量补质量" / "明天安排恢复").

**Subjective capture (absorbs Training Follow-Up):** when RPE/pain feedback is missing — always, for the first push about a session — the review ends with ONE short question (RPE / pain / how it felt). Her reply goes through Riko into `training_feedback` (existing path). No reply → never re-asked (existing once-only contract). The old `_maybe_trigger_training_followup` call in cmd_sync is removed — the review supersedes it; the Follow-Up cron and prompt stay registered but dormant.

## 4. Riko prompt (`~/ai/prompts/session-review.md`, managed by sync-cron-prompts.sh)

Product form per the established push rules: ≤7 lines, no schema labels. Line 1 = session verdict (quality/PR/notable fact). 2-3 data facts max. One next-time line. Optional single question at the end. Coach Contract included. Numbers only from the signal file.

## 5. Out of scope

- Reviews for walking or sub-15-minute records.
- Quiet hours (she trains evenings and sleeps late; dedup prevents repeats; the file ages out naturally).
- Any new manual logging — the question is optional and asked once.

## 6. Testing

TDD per calculator with seeded fixtures; dedup tested (one review per activity, ever); cmd_sync hook exception-guarded like all detector hooks; backtest = generate review blocks for her 5 most recent real sessions and human-review before the prompt goes live; deploy with prompt-source workflow (edit ~/ai/prompts + sync script + ~/ai commit) — never direct cron edit.
