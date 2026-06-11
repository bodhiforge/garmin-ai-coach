# Insight Discovery Pipeline & Coach Contract — Design

Date: 2026-06-10
Status: draft for review

## 1. Vision

Transform garmin-ai-coach from a reactive daily-prescription system into a proactive professional coach that:

- **knows the athlete deeply** — every output is conditioned on her body, history, and context, never stranger advice;
- **discovers things she doesn't know about herself** — personal pattern mining with statistical rigor (the Whoop/Oura "aha moment" mechanism);
- **warns early** — multi-signal illness/overreach detection;
- **speaks first** — pushes insights and warnings proactively instead of waiting to be asked.

All of this uses passive Garmin data already in the database. No new logging burden (one-time setup inputs are acceptable; recurring manual entry is not).

The architecture principle is unchanged: **Python computes every number. The LLM never does math.** It presents validated, pre-computed insights in the coach's voice.

## 2. Coach Contract

This section governs every output of the system — insights, prescriptions, warnings, and chat answers.

### 2.1 Knows-the-athlete requirement

Every detection and presentation stage receives the **Athlete Model**, assembled from existing sources (`user_model.py` computed model, `profile.md`, `training_feedback`, the insights store):

- **Physiological baselines** — personal HRV range, RHR, sleep norms, readiness factor weights.
- **Injury & pain map** — low-back/waist sensitivity, rib flare, shoulder position, right-ankle balance work; sourced from `training_feedback` and documented constraints.
- **Hard constraints** — menstrual rules (no swim during active period), pain ≥4/10 or RPE ≥8 overrides progression.
- **Preferences & rhythm** — basketball Wed/Fri evenings, no easy-walk recommendations, home micro-session menu, equipment defaults.
- **Goals** — basketball performance, ski season readiness, posture correction, general strength.

A recommendation that ignores any relevant field of the Athlete Model is a defect, even if generically correct.

### 2.2 Authority hierarchy

```
hard constraints > professional judgment > preferences > generic advice
```

1. **Hard constraints are never violated.**
2. **Professional judgment may override preferences.** When the data supports a recommendation that conflicts with a stated preference, the coach must not silently drop it. It delivers the adapted version and says so explicitly: name the preference, the evidence, and the professional call. Example: "你偏好髋主导训练 — 但你的动作矩阵显示 squat 模式为零,结合腰部情况,这是适配你的低负荷膝主导方案。"
3. **Preferences shape HOW, never WHETHER.** A structural need (e.g., missing movement pattern) is addressed through preference-compatible variants, not skipped.
4. **Generic textbook advice is the floor, never the output.** Every number presented comes from her data; population reference points are explicitly framed as such.

## 3. Architecture

```
*/30 sync cron (existing)
      │
      ▼
Detection layer (pure Python)
  observations.py (revived) · discovery.py · warning composite · strength_profile.py · basketball detectors
      │  statistical gate: n + effect size + significance
      ▼
Insights store (DB table, status state machine)
      │
      ├──▶ Instant push (warnings, via existing Riko channel)
      ├──▶ Weekly insight card (Saturday Deep Review)
      ├──▶ Monthly progression narrative (first-Saturday Deep Review)
      │
      ▼
Feedback loop: adopted insights become coach-layer rule inputs
```

Entry point: `cmd_sync` state machine (already running every 30 minutes). Judgment point: the statistical gate in the detection layer — the LLM only ever sees validated findings.

## 4. Detection layer

### 4.1 Revive `observations.py` (zero new infrastructure)

`detect_observations` is currently only called from `cmd_reflect`, whose cron was removed when Neve was retired — the module has been orphaned since 2026-03-28. Move the call into the `cmd_sync` path. Its six existing detectors (ski fatigue pattern, schedule pattern, rest compliance, recovery-by-activity-type, sleep–training correlation, consecutive-day impact) resume producing immediately.

### 4.2 `discovery.py` — correlation miner

Lagged-correlation mining over the candidate space:

- activity (type, timing, load) × next-night sleep stages and next-day HRV/RHR/readiness/Body Battery;
- menstrual phase × physiological baselines;
- sleep debt × next-day performance and RPE;
- consecutive training days × readiness cost;
- weather × outdoor performance.

**Statistical gate (hard requirement):** paired samples n ≥ 8, effect-size threshold, permutation significance test. Every surfaced insight carries its evidence (`n`, magnitude), in the style of the existing "-26%, 3/7 sessions" outputs. Below-gate findings persist as `candidate` and are automatically re-evaluated as data accumulates.

### 4.3 Illness / overreach early-warning composite

≥2 of 4 signals deviating from personal baseline — respiration rate, resting HR, HRV, sleep quality — triggers a warning. Distinct from the existing per-metric 2-sigma anomaly detection: this is a composite "your body is fighting something" judgment. Hysteresis plus a 48-hour per-type cooldown prevents nagging.

### 4.4 `strength_profile.py` — Strength Intelligence (priority 1)

Five calculators over `gym_sets` + `manual_gym_sets` (314 sets, 29 exercises, 73% with load, 100% with rest duration as of 2026-06-10):

| Calculator | Question it answers |
|---|---|
| e1RM trend + plateau detection (Epley) per exercise | "Should I add weight?" — flat e1RM for N weeks ⇒ plateau ⇒ concrete prescription (load +5% / rep-scheme change / variation) |
| Weekly sets per muscle group vs volume landmarks (MEV/MAV framework) | "Am I doing enough / too much?" |
| Movement pattern coverage matrix (squat/hinge/lunge/push-h/push-v/pull-h/pull-v/carry/core) | "Is my exercise selection right?" — e.g., current data shows ~99 pull sets vs ~25 push sets, zero squat-pattern work |
| Rep-zone distribution vs goals | "Are my sets and reps sensible?" — currently 0% of sets in the ≤6-rep strength zone |
| Rest-interval analysis (`rest_duration_sec`, currently unused) | "Are my rests right for my goal?" |

Data-quality guards: filter non-lift rows (e.g., Treadmill entries inside `gym_sets`), merge `manual_gym_sets` without being clobbered by Garmin API refresh (existing contract), respect post-hoc Garmin Connect edits via the existing 14-day refresh.

**Dual outlet, one computation:** outputs feed the discovery pipeline (structural findings become weekly insight cards) AND are exposed as an agent tool so interactive questions ("我的 RDL 怎么样了?") are answered from her real curves.

### 4.5 Basketball detectors (priority 2)

In-session HR drift (conditioning fade across the session), HR-zone distribution trend across sessions, day-after readiness cost profile. Data already present: 13 sessions / 1407 min in the last 90 days with zones, load, and FIT files.

Hiking module is out of scope for now; **trigger to revisit:** discovery flags hiking reaching n ≥ 8 sessions in a rolling 90 days and auto-suggests enabling a hiking module in the weekly card.

## 5. Insights store

New table `insights`:

```
id · discovered_date · category · statement · evidence_json (n, effect, test)
· status: candidate → validated → surfaced → adopted
· surfaced_date · adopted_rule_ref
```

`observations.md` stays as a human/LLM-readable mirror regenerated from the table (the chat agent already loads it), but the table is the source of truth. Dedup logic moves from string matching on the .md file to keyed rows.

## 6. Surfacing cadences — all on existing triggers, no new cron entries

| Cadence | Trigger (existing) | Content | Throttle |
|---|---|---|---|
| Instant | every */30 `cmd_sync` | warning composite fired → immediate Riko push ("呼吸率和 RHR 同时偏高,今天篮球建议降级") | 48h cooldown per warning type, `notifications` table dedup |
| Weekly | Saturday Training Deep Review | max 1 new validated insight as a "Did you know" card | 1/week, deliberately scarce |
| Monthly | first-Saturday Deep Review | progression narrative: VO2max / endurance score trends, `_progression_trajectory`, `_blind_spots` made proactive | 1/month |

Presentation follows the existing push product-quality bar: recommendation-first, no schema labels, compact body-metrics strip, anomalies flagged inline.

## 7. Feedback loop (adopted insights become rules)

- Personal recovery-cost per activity replaces the generic `_estimated_recovery_hours` estimates.
- Strength findings personalize `exercise_progression_layer` (priority list and progression rules driven by her e1RM curves, not the hardcoded list).
- `weekly_programming_layer` spacing uses measured recovery costs (e.g., basketball's 2.6× HRV cost vs skiing).
- RPE/pain capture: post-session pushes gently prompt a one-line reply (the `training_feedback` table has 1 row — underused; the existing one-time follow-up contract applies, no nagging).

## 8. Implementation decisions

- **Step 0 — reconcile repo state.** mini is 20 commits ahead of origin with a dirty working tree (4 modified files in `src/ai/`); macp local is 1 divergent commit ahead; GitHub is behind both. Mini's lineage is production truth: commit mini's WIP, push mini → reconcile macp's local commit on top → then develop locally, deploy on mini via `git pull`. No direct edits on mini's production checkout thereafter.
- **Single outlet preserved:** instant warnings reuse the existing "Riko Training Push"-style `openclaw cron run` mechanism + `notifications` dedup. No new bot, no new channel.
- **No new cron entries.** All cadences ride existing triggers (sync */30, Saturday Deep Review).

## 9. Testing

- Unit tests per calculator with fixture databases, following the repo's existing test patterns.
- Statistical-gate tests with synthetic data containing known planted effects (and known nulls).
- **Backtest before enabling push:** run the full discovery pipeline against the existing ~3 months of real data; manually review every produced insight for quality and tone before any of it reaches Telegram.

## 10. Out of scope

- New hardware/data sources; any recurring manual logging; dashboards/UI.
- Hiking module (data-driven re-entry trigger defined in §4.5).
- Changes to the morning Training Push decision flow itself (this design adds layers around it, not inside it).
