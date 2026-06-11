from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Load config early to set API key before PydanticAI agent import
_pre_config_path = os.environ.get("GARMIN_COACH_CONFIG", "config.yaml")
if os.path.exists(_pre_config_path):
    import yaml
    with open(_pre_config_path) as _f:
        _raw = yaml.safe_load(_f)
    _llm = _raw.get("llm", {})
    os.environ.setdefault("OPENAI_API_KEY", _llm.get("api_key", ""))
    os.environ.setdefault("COACH_AGENT_MODEL", f"openai:{_llm.get('model', '')}")

from .config import load_config
from .db.models import Database
from .garmin.client import GarminClient
from .garmin.sync import GarminSync
from .ai.coach import AICoach
from .bot.telegram import CoachBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
DEFAULT_RIKO_TRAINING_PUSH_CRON_ID = "bff6527a-9d3c-4b1b-acac-8f06a63fa1dc"
RIKO_TRAINING_FOLLOWUP_CRON_NAME = "Riko Training Follow-Up"


def _riko_training_push_cron_id() -> str:
    return os.environ.get("RIKO_TRAINING_PUSH_CRON_ID", DEFAULT_RIKO_TRAINING_PUSH_CRON_ID)


def _openclaw_cron_id_by_name(openclaw_bin: str, cron_path: str, name: str) -> str | None:
    import json
    import subprocess

    try:
        result = subprocess.run(
            [openclaw_bin, "cron", "list", "--json", "--all"],
            env={**os.environ, "PATH": cron_path},
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as error:
        print(f"WARNING: Riko cron discovery failed: {error}")
        return None

    if result.returncode != 0:
        print(f"WARNING: Riko cron discovery failed: {result.stderr[:200]}")
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        print(f"WARNING: Riko cron discovery returned invalid JSON: {error}")
        return None

    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    return next(
        (str(job.get("id")) for job in jobs if job.get("name") == name and job.get("id")),
        None,
    )


def _riko_training_followup_cron_id(openclaw_bin: str, cron_path: str) -> str | None:
    return os.environ.get("RIKO_TRAINING_FOLLOWUP_CRON_ID") or _openclaw_cron_id_by_name(
        openclaw_bin,
        cron_path,
        RIKO_TRAINING_FOLLOWUP_CRON_NAME,
    )


def build_components(config_path: str | None = None):
    config = load_config(config_path)

    db = Database(config.data_dir / "garmin.db")

    garmin_client = GarminClient(
        email=config.garmin.email,
        password=config.garmin.password,
    )

    sync = GarminSync(
        client=garmin_client,
        db=db,
        data_dir=config.data_dir,
    )

    coach = AICoach(
        api_key=config.llm.api_key,
        model=config.llm.model,
        db=db,
        base_url=config.llm.base_url,
        data_dir=config.data_dir,
    )

    bot = CoachBot(
        bot_token=config.telegram.bot_token,
        chat_id=config.telegram.chat_id,
        coach=coach,
        sync=sync,
    )

    return config, db, garmin_client, sync, coach, bot


def cmd_bot(args: argparse.Namespace) -> None:
    """Run the Telegram bot (long-running)."""
    _, _, _, _, _, bot = build_components(args.config)
    bot.run()


def _write_training_digest(config, sync, coach, metrics) -> None:
    """Write training digest file for OpenClaw consumption."""
    from datetime import date, timedelta
    from .ai.insights import daily_summary

    # If today has no sleep data, use yesterday's
    if metrics.get("sleep_duration_min") is None:
        yesterday = date.today() - timedelta(days=1)
        metrics = sync.client.get_daily_metrics(yesterday)

    computed = daily_summary(coach.db, metrics)

    from .ai.coach import _format_metrics
    raw_metrics = _format_metrics(metrics)

    plan_path = Path.home() / "ai" / "data" / "fitness-plan.md"
    fitness_plan = plan_path.read_text().strip() if plan_path.exists() else ""

    memory_context = coach.get_memory()

    digest_path = Path.home() / "ai" / "data" / "signals" / "training-digest.txt"
    digest_path.parent.mkdir(parents=True, exist_ok=True)

    digest = f"""# Training Digest — {date.today()} ({date.today().strftime('%A')})
# Generated: {date.today().isoformat()} by Garmin backend (pure data, no LLM)
# Consumed by: Riko OpenClaw training cron → Telegram

## Raw Metrics
{raw_metrics}

## Computed Analysis
{computed}

## Fitness Plan
{fitness_plan}

## User Memory
{memory_context if memory_context else "No memory files."}
"""
    digest_path.write_text(digest)
    print(f"Digest written to {digest_path} ({len(digest)} chars)")


def _trigger_riko_analysis() -> bool:
    """Trigger Riko (OpenClaw) to generate morning report. Returns True if triggered."""
    import shutil
    import subprocess
    riko_cron_id = _riko_training_push_cron_id()
    cron_path = os.pathsep.join([
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        os.environ.get("PATH", ""),
    ])
    openclaw_bin = shutil.which("openclaw", path=cron_path) or "/opt/homebrew/bin/openclaw"
    try:
        result = subprocess.run(
            [openclaw_bin, "cron", "run", riko_cron_id],
            env={**os.environ, "PATH": cron_path},
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"Riko training push triggered (cron {riko_cron_id})")
            return True
        print(f"WARNING: Riko trigger failed: {result.stderr[:200]}")
    except Exception as e:
        print(f"WARNING: Riko trigger failed: {e}")
    return False


def _trigger_riko_training_followup() -> bool:
    """Trigger Riko (OpenClaw) to ask for missing post-workout data."""
    import shutil
    import subprocess
    cron_path = os.pathsep.join([
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        os.environ.get("PATH", ""),
    ])
    openclaw_bin = shutil.which("openclaw", path=cron_path) or "/opt/homebrew/bin/openclaw"
    riko_cron_id = _riko_training_followup_cron_id(openclaw_bin, cron_path)
    if riko_cron_id is None:
        print(f"WARNING: Riko follow-up trigger failed: {RIKO_TRAINING_FOLLOWUP_CRON_NAME} cron not found by name")
        return False
    try:
        result = subprocess.run(
            [openclaw_bin, "cron", "run", riko_cron_id],
            env={**os.environ, "PATH": cron_path},
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"Riko training follow-up triggered (cron {riko_cron_id})")
            return True
        print(f"WARNING: Riko follow-up trigger failed: {result.stderr[:200]}")
    except Exception as e:
        print(f"WARNING: Riko follow-up trigger failed: {e}")
    return False


def _riko_training_delivery_confirmed(triggered_mtime: float) -> bool:
    """Return True only when OpenClaw says this training push was delivered."""
    import json
    import shutil
    import subprocess
    riko_cron_id = _riko_training_push_cron_id()
    cron_path = os.pathsep.join([
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        os.environ.get("PATH", ""),
    ])
    openclaw_bin = shutil.which("openclaw", path=cron_path) or "/opt/homebrew/bin/openclaw"
    try:
        result = subprocess.run(
            [openclaw_bin, "cron", "runs", "--id", riko_cron_id, "--limit", "5"],
            env={**os.environ, "PATH": cron_path},
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"WARNING: Riko delivery check failed: {result.stderr[:200]}")
            return False
        entries = json.loads(result.stdout).get("entries", [])
    except Exception as e:
        print(f"WARNING: Riko delivery check failed: {e}")
        return False

    trigger_ms = int(triggered_mtime * 1000)
    return any(
        entry.get("jobId") == riko_cron_id
        and entry.get("action") == "finished"
        and entry.get("status") == "ok"
        and (
            entry.get("delivered") is True
            or entry.get("deliveryStatus") == "delivered"
            or entry.get("delivery", {}).get("delivered") is True
        )
        and int(entry.get("ts") or entry.get("runAtMs") or 0) >= trigger_ms
        for entry in entries
    )


def _refresh_recent_gym_sets(sync, coach) -> None:
    """Re-fetch gym_sets from Garmin API after post-workout manual edits.

    The watch auto-detects reps during a session, but weight + exercise name are
    filled in Garmin Connect afterwards. The first sync may run before those
    edits land. This catches them before the morning digest is built.
    """
    refreshed = sync.refresh_recent_gym_sets(days=14)
    if refreshed > 0:
        print(f"Refreshed edited gym_sets for {refreshed} recent strength session(s)")


def _strength_followup_signal(db: Database) -> tuple[str, str] | None:
    """Return (notification_type, signal) when a post-strength follow-up is needed."""
    activities = db.get_recent_activities(days=2, activity_type="strength")
    if not activities:
        return None

    activity = activities[0]
    activity_id = activity["id"]
    notification_type = f"strength_followup_{activity_id}"
    if db.get_last_notification(notification_type) is not None:
        return None

    sets = db.get_gym_sets(activity_id)
    feedback = db.get_training_feedback(activity_id)
    strength_sets = [
        set_row for set_row in sets
        if (set_row.get("reps") or 0) > 0
        and str(set_row.get("exercise") or "").lower() not in ("unknown", "treadmill", "cardio")
    ]
    set_count = len(strength_sets)
    weighted_sets = sum(
        1 for set_row in strength_sets
        if set_row.get("weight_lb") is not None and set_row.get("weight_lb") > 0
    )
    weight_capture_rate = weighted_sets / set_count if set_count > 0 else 0

    needs = []
    if set_count == 0:
        needs.append("exercise list / reps because Garmin has no usable set detail")
    elif weight_capture_rate < 0.6:
        needs.append(f"loads for main lifts (weight capture {weighted_sets}/{set_count})")
    if feedback is None:
        needs.append("session RPE")
        needs.append("ankle/knee/low-back pain 0-10")

    duration_min = activity.get("duration_min") or 0
    training_load = activity.get("training_load") or 0
    z4z5_min = ((activity.get("hr_zone4_sec") or 0) + (activity.get("hr_zone5_sec") or 0)) / 60
    if duration_min >= 75 or set_count >= 20 or training_load >= 100 or z4z5_min >= 8:
        needs.append("how hard it felt after the high-volume session")

    if not needs:
        return None

    exercise_names = []
    for set_row in strength_sets:
        exercise = str(set_row.get("exercise") or "").strip()
        if exercise and exercise not in exercise_names:
            exercise_names.append(exercise)

    signal_path = Path.home() / "ai" / "data" / "signals" / "training-followup.txt"
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal = "\n".join([
        f"# Training Follow-Up Signal — {activity.get('date')}",
        f"Activity ID: {activity_id}",
        "Type: strength",
        f"Duration: {duration_min:.0f} min",
        f"Training load: {training_load:.0f}",
        f"Strength sets: {set_count}",
        f"Weighted sets: {weighted_sets}/{set_count}",
        f"Z4/Z5: {z4z5_min:.0f} min",
        f"Known exercises: {', '.join(exercise_names[:8]) if exercise_names else 'unknown'}",
        f"Missing pieces to ask for: {', '.join(needs)}",
        "",
        "Ask one concise follow-up. Do not ask for everything if the user already provided feedback.",
        "Explain that the answer controls the next progression/volume decision.",
    ])
    signal_path.write_text(signal)
    return notification_type, signal


def _maybe_trigger_training_followup(sync: GarminSync, *, dry_run: bool) -> None:
    signal = _strength_followup_signal(sync.db)
    if signal is None:
        return

    notification_type, content = signal
    print(f"Training follow-up needed: {notification_type}")
    print(content)
    if dry_run:
        return

    if _trigger_riko_training_followup():
        sync.db.add_notification(notification_type, content)


def _copy_legacy_flag_timestamp(legacy_path: Path, current_path: Path) -> None:
    """Create a renamed flag with the legacy flag's timestamp."""
    current_path.touch()
    legacy_stat = legacy_path.stat()
    os.utime(current_path, (legacy_stat.st_atime, legacy_stat.st_mtime))


def _migrate_legacy_training_flags(flag_dir: Path, today: str) -> None:
    """Keep today's legacy flags from causing duplicate pushes."""
    mappings = [
        (flag_dir / f"neve-pushed-{today}", flag_dir / f"training-pushed-{today}"),
        (flag_dir / f"neve-triggered-{today}", flag_dir / f"training-triggered-{today}"),
    ]
    for legacy_path, current_path in mappings:
        if legacy_path.exists() and not current_path.exists():
            _copy_legacy_flag_timestamp(legacy_path, current_path)


def _write_training_data(metrics: dict) -> None:
    """Write today's wakeup metrics for Riko to read."""
    import json
    from datetime import date
    data_path = Path.home() / "ai" / "data" / "training-today.json"
    data = {
        "date": str(date.today()),
        "sleep_end": metrics.get("sleep_end", ""),
        "readiness_score": metrics.get("training_readiness_score", 0),
        "readiness_level": metrics.get("training_readiness_level", ""),
        "hrv": metrics.get("hrv_last_night", 0),
        "sleep_score": metrics.get("sleep_score", 0),
        "body_battery_am": metrics.get("body_battery_am", 0),
        "bb_at_wake": metrics.get("bb_at_wake", 0),
        "acwr": metrics.get("acwr_ratio", 0),
        "stress_avg": metrics.get("stress_avg", 0),
        "resting_hr": metrics.get("resting_hr", 0),
        "sleep_min": metrics.get("sleep_duration_min", 0),
    }
    data_path.write_text(json.dumps(data))
    print(f"Wakeup data written to {data_path}")


def cmd_sync(args: argparse.Namespace) -> None:
    """Sync Garmin data. Manages morning push: detect wake -> trigger Riko -> mark delivery."""
    from datetime import date
    config, _, _, sync, coach, _bot = build_components(args.config)

    metrics = sync.sync_daily_metrics()
    print(f"Daily metrics synced: HRV={metrics.get('hrv_last_night')}ms, "
          f"RHR={metrics.get('resting_hr')}bpm")

    new_activities = sync.sync_activities()
    print(f"Activities synced: {len(new_activities)} new")
    _refresh_recent_gym_sets(sync, coach)
    _maybe_trigger_training_followup(sync, dry_run=False)

    # Pattern detection runs every sync; keyed dedup makes it idempotent.
    # Detection must never break the sync state machine.
    from .ai.observations import detect_observations
    try:
        new_observations = detect_observations(sync.db, coach.memory_dir)
        for observation in new_observations:
            print(f"New observation: {observation}")
    except Exception as error:
        logger.warning("Observation detection failed: %s", error)

    # Strength findings are gated and key-deduped; cheap to run every sync.
    from .ai.strength_profile import store_strength_findings
    try:
        new_findings = store_strength_findings(sync.db)
        if new_findings:
            print(f"New strength findings stored: {new_findings}")
    except Exception as error:
        logger.warning("Strength finding detection failed: %s", error)

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

    # Saturday: surface at most one validated insight for the Deep Review.
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

    # --- Morning push state machine ---
    today = str(date.today())
    flag_dir = Path.home() / "ai" / "data"
    _migrate_legacy_training_flags(flag_dir, today)
    flag_sent = flag_dir / f"training-pushed-{today}"
    flag_triggered = flag_dir / f"training-triggered-{today}"
    report_path = flag_dir / "signals" / "morning-report.txt"

    # Already sent today? Done.
    if flag_sent.exists():
        return

    # Phase 1: Detect wakeup -> write data + trigger Riko
    # Always create flag_triggered on wake detection so Phase 3 can retry
    # even if the initial trigger fails (e.g., node missing from cron PATH).
    if not flag_triggered.exists() and getattr(sync, '_last_wake_detected', False):
        print("Wake-up detected — writing data and triggering Riko...")
        _write_training_data(metrics)
        _write_training_digest(config, sync, coach, metrics)
        report_path.unlink(missing_ok=True)
        flag_triggered.touch()
        _trigger_riko_analysis()
        return

    # Phase 2: Report ready -> Riko already delivered it; mark local state.
    if flag_triggered.exists() and report_path.exists():
        import os
        report_mtime = os.path.getmtime(report_path)
        flag_mtime = os.path.getmtime(flag_triggered)
        # Report must be newer than trigger (this run, not stale)
        if report_mtime > flag_mtime:
            report = report_path.read_text().strip()
            if report:
                if not _riko_training_delivery_confirmed(flag_mtime):
                    print("Riko report exists but Telegram delivery is not confirmed yet")
                else:
                    try:
                        flag_sent.touch()
                        print(f"Morning push delivered by Riko; marked sent ({len(report)} chars)")

                        # Snapshot to history/ so the Saturday Training Deep Review
                        # can audit the week's actual morning calls.
                        history_dir = flag_dir / "signals" / "history"
                        history_dir.mkdir(parents=True, exist_ok=True)
                        snapshot = history_dir / f"{today}.txt"
                        snapshot.write_text(report)
                        # Prune snapshots older than 30 days.
                        for f in history_dir.glob("*.txt"):
                            try:
                                snap_date = date.fromisoformat(f.stem)
                                if (date.today() - snap_date).days > 30:
                                    f.unlink(missing_ok=True)
                            except ValueError:
                                pass

                        # Cleanup old flags
                        for f in flag_dir.glob("training-pushed-*"):
                            if f.name != flag_sent.name:
                                age = (date.today() - date.fromisoformat(f.name.replace("training-pushed-", ""))).days
                                if age > 7:
                                    f.unlink(missing_ok=True)
                        for f in flag_dir.glob("training-triggered-*"):
                            if f.name != flag_triggered.name:
                                f.unlink(missing_ok=True)
                    except Exception as e:
                        print(f"ERROR: Riko delivery bookkeeping failed: {e} — will retry next sync")
                    return

    # Phase 3: Triggered but no report yet -> retry trigger if stale
    if flag_triggered.exists() and not flag_sent.exists():
        import os, time
        trigger_age = time.time() - os.path.getmtime(flag_triggered)
        if trigger_age > 600:  # 10 min without report = retry
            print("Riko report overdue (>10min) — retrying trigger...")
            if _trigger_riko_analysis():
                flag_triggered.touch()  # reset timer


def cmd_morning(args: argparse.Namespace) -> None:
    """Sync data + write training digest to file. No LLM call.
    The digest is consumed by Riko's OpenClaw training cron."""
    config, _, _, sync, coach, bot = build_components(args.config)

    metrics = sync.sync_daily_metrics()
    sync.sync_activities()
    _refresh_recent_gym_sets(sync, coach)

    _write_training_digest(config, sync, coach, metrics)

    if args.dry_run:
        from pathlib import Path
        digest_path = Path.home() / "ai" / "data" / "signals" / "training-digest.txt"
        digest = digest_path.read_text()
        print("\n--- DIGEST PREVIEW ---")
        print(digest[:2000])
        if len(digest) > 2000:
            print(f"\n... ({len(digest) - 2000} more chars)")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze the most recent activity."""
    _, db, _, sync, coach, bot = build_components(args.config)

    # Sync first to get latest
    new_activities = sync.sync_activities()
    _refresh_recent_gym_sets(sync, coach)
    if not new_activities:
        print("No new activities to analyze.")
        return

    activity = new_activities[0]
    activity_type = activity["type"]
    activity_id = activity["id"]

    print(f"Analyzing: {activity['date']} {activity_type} ({activity.get('duration_min')}min)")

    analysis = None
    if activity_type == "strength":
        sets = db.get_gym_sets(activity_id)
        if sets:
            analysis = coach.post_gym_analysis(activity, sets)
    elif activity_type == "skiing":
        runs = db.get_ski_runs(activity_id)
        if runs:
            analysis = coach.post_ski_analysis(activity, runs)
    else:
        print(f"No specialized analysis for activity type: {activity_type}")
        return

    if analysis is not None:
        print(analysis)
        if not args.dry_run:
            asyncio.run(bot.send_message(analysis))
            print("\nSent to Telegram.")
    else:
        print("No detailed data available for this activity.")


def cmd_impact(args: argparse.Namespace) -> None:
    """Generate coach effectiveness report."""
    _, db, _, sync, _, _ = build_components(args.config)

    sync.sync_daily_metrics()
    sync.sync_activities()
    sync.refresh_recent_gym_sets(days=14)

    from .ai.impact import impact_report
    report = impact_report(db, days=args.days)
    print(report)


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


MONTHLY_NARRATIVE_COOLDOWN_HOURS = 21 * 24  # >2 weeks ⇒ once per month in practice


def _write_monthly_narrative(db, target_path: Path | None = None) -> bool:
    """Compose the monthly progression narrative inputs from the computed
    user model. Pure data file — Riko writes the prose."""
    from datetime import date
    if db.hours_since_last_notification("monthly_narrative") < MONTHLY_NARRATIVE_COOLDOWN_HOURS:
        return False
    from .ai.user_model import build_user_model
    target = target_path or (Path.home() / "ai" / "data" / "signals" / "monthly-narrative.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_user_model(db))
    db.add_notification("monthly_narrative", str(date.today()))
    return True


HEALTH_ALERT_COOLDOWN_HOURS = 48
DEFAULT_RIKO_HEALTH_ALERT_CRON_NAME = "Riko Health Alert"


def _write_health_alert(db, warning: dict, alert_path: Path | None = None) -> bool:
    """Persist the computed warning for Riko and record the cooldown.
    Returns True when a fresh alert was written."""
    if db.hours_since_last_notification("health_warning") < HEALTH_ALERT_COOLDOWN_HOURS:
        return False
    target = alert_path or (Path.home() / "ai" / "data" / "signals" / "health-alert.txt")
    lines = [f"# Health Warning — {warning['date']}", ""]
    lines.append(
        f"Fired signals (adverse, ≥1.5σ vs 28d baseline): {', '.join(warning['fired_signals'])}"
    )
    for metric, info in warning["details"].items():
        marker = "  <-- FIRED" if metric in warning["fired_signals"] else ""
        lines.append(
            f"- {metric}: {info['value']} (baseline {info['baseline']}, z={info['z']}){marker}"
        )
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
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
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


def cmd_strength_profile(args: argparse.Namespace) -> None:
    """Print the computed strength profile. Consumed by Riko for Q&A.
    Reads the DB directly — no Garmin client, so it works offline and
    never triggers a login."""
    config = load_config(args.config)
    db = Database(config.data_dir / "garmin.db")
    from .ai.strength_profile import strength_profile_block, strength_structural_findings

    print(strength_profile_block(db, days=args.days))
    findings = strength_structural_findings(db, days=args.days)
    if findings:
        print("\n## Structural Findings")
        for finding in findings:
            print(f"- {finding['statement']}")


def cmd_insight(args: argparse.Namespace) -> None:
    """List/adopt/dismiss insights. Riko calls adopt/dismiss on Bodhi's word."""
    config = load_config(args.config)
    db = Database(config.data_dir / "garmin.db")
    if args.action == "list":
        for row in db.get_insights(status=args.status):
            print(f"[{row['status']:>9}] {row['key']}: {row['statement']}")
        return
    if args.key is None:
        print("insight adopt/dismiss requires a key")
        return
    matches = [row for row in db.get_insights() if row["key"] == args.key]
    if not matches:
        print(f"No insight with key {args.key}")
        return
    if args.action == "adopt":
        db.mark_insight_adopted(matches[0]["id"], rule_ref=args.rule or "manual")
        print(f"Adopted: {args.key}")
    elif args.action == "dismiss":
        db.mark_insight_dismissed(matches[0]["id"])
        print(f"Dismissed: {args.key}")


def cmd_basketball_profile(args: argparse.Namespace) -> None:
    """Print the computed basketball profile. Consumed by Riko for Q&A."""
    config = load_config(args.config)
    db = Database(config.data_dir / "garmin.db")
    from .ai.basketball_profile import basketball_profile_block
    print(basketball_profile_block(db, days=args.days))


def cmd_whoami(args: argparse.Namespace) -> None:
    """Show computed user model — what the system knows about you."""
    _, db, _, sync, _, _ = build_components(args.config)

    sync.sync_daily_metrics()
    sync.sync_activities()
    sync.refresh_recent_gym_sets(days=14)

    from .ai.user_model import build_user_model
    from .ai.anomaly import detect_anomalies, format_anomalies

    model = build_user_model(db)
    print(model)

    anomalies = detect_anomalies(db)
    if anomalies:
        print(f"\n{format_anomalies(anomalies)}")


def _build_activity_analysis(
    events: list[str], db: Database, coach: AICoach,
) -> str | None:
    """If events include a new activity, return full analysis text."""
    ski_event = any("ski" in e.lower() for e in events)
    gym_event = any("gym" in e.lower() for e in events)

    if not ski_event and not gym_event:
        return None

    latest = db.get_recent_activities(days=2)
    if not latest:
        return None

    activity = latest[0]
    if ski_event and activity["type"] == "skiing":
        runs = db.get_ski_runs(activity["id"])
        if runs:
            return coach.post_ski_analysis(activity, runs)
    elif gym_event and activity["type"] == "strength":
        sets = db.get_gym_sets(activity["id"])
        if sets:
            return coach.post_gym_analysis(activity, sets)

    return None


def cmd_reflect(args: argparse.Namespace) -> None:
    """Smart sync + event-driven notifications."""
    config, _, _, sync, coach, bot = build_components(args.config)

    try:
        _run_reflect(sync, coach, bot, dry_run=args.dry_run)
    except Exception as e:
        logger.error("Reflect failed: %s", e, exc_info=True)
        if not args.dry_run:
            try:
                asyncio.run(bot.send_message(f"⚠️ Coach reflect failed: {type(e).__name__}"))
            except Exception:
                pass
        raise


def _run_reflect(sync: GarminSync, coach: AICoach, bot, *, dry_run: bool) -> None:
    """Core reflect logic, separated for error handling."""
    # Smart sync with merge
    sync.sync_daily_metrics()
    sync.sync_activities()
    sync.refresh_recent_gym_sets(days=14)
    _maybe_trigger_training_followup(sync, dry_run=dry_run)

    # Detect behavioral patterns and save to observations.md
    from .ai.observations import detect_observations
    new_obs = detect_observations(sync.db, coach.memory_dir)
    if new_obs:
        for obs in new_obs:
            print(f"New observation: {obs}")

    # Open-ended anomaly detection
    from .ai.anomaly import detect_anomalies
    anomalies = detect_anomalies(sync.db)
    for a in anomalies:
        print(f"Anomaly: {a['description']}")

    # Event-driven notification (Python decides, LLM writes copy)
    from .ai.notify import should_notify
    should_send, events, score = should_notify(sync.db)
    print(f"Events: {events} | Score: {score} | Send: {should_send}")

    if should_send:
        # Check if there's a new activity — send full analysis
        message = _build_activity_analysis(events, sync.db, coach)
        if message is None:
            # No activity event — template notification (no LLM call)
            message = "\U0001f4ca " + " | ".join(events)
        print(f"Message: {message}")
        if not dry_run:
            for event in events:
                event_type = event.split(":")[0].strip().lower().replace(" ", "_")
                if "activity" in event_type:
                    activities = sync.db.get_recent_activities(days=1)
                    if activities:
                        event_type = f"activity_{activities[0]['id']}"
                elif "pr" in event_type:
                    event_type = "ski_pr"
                elif "hrv" in event_type:
                    event_type = "hrv_alert"
                elif "rhr" in event_type:
                    event_type = "rhr_alert"
                elif "training" in event_type or "inactive" in event_type:
                    event_type = "inactive"
                sync.db.add_notification(event_type, message)
            asyncio.run(bot.send_message(message))
            print("Sent to Telegram.")
    else:
        print("Nothing to report.")



def cmd_concern(args: argparse.Namespace) -> None:
    """Manage training concerns."""
    config, db_unused, _, _, _, _ = build_components(args.config)
    from .db.models import Database
    db = Database(config.data_dir / "garmin.db")

    if args.action == "add":
        concern_id = db.upsert_concern(
            concern=args.text,
            impact=args.impact,
            sport_affected=args.sport,
            source=args.source or "user",
        )
        print(f"Added concern #{concern_id}: {args.text}")

    elif args.action == "list":
        concerns = db.get_active_concerns()
        if not concerns:
            print("No active concerns.")
        else:
            for c in concerns:
                print(f"  #{c['id']} [{c['created_date']}] {c['concern']}")
                if c.get("impact"):
                    print(f"      Impact: {c['impact']}")
                if c.get("sport_affected"):
                    print(f"      Sport: {c['sport_affected']}")

    elif args.action == "resolve":
        db.resolve_concern(int(args.concern_id))
        print(f"Resolved concern #{args.concern_id}")


def cmd_push_workout(args: argparse.Namespace) -> None:
    """Push a workout plan JSON to Garmin Connect."""
    import json as _json
    config, _, garmin_client, _, _, _ = build_components(args.config)

    plan = _json.loads(args.plan)

    from .garmin.workout import upload_workout, format_plan_text
    print(format_plan_text(plan))

    workout_id = upload_workout(garmin_client, plan)
    if workout_id is not None:
        print(f"\nUploaded to Garmin (id: {workout_id})")

        from .garmin.workout import load_workout_tracker, save_workout_tracker
        tracker = load_workout_tracker(config.data_dir)
        tracker[plan.get("name", "unnamed")] = {
            "workout_id": workout_id,
            "plan": plan,
        }
        save_workout_tracker(config.data_dir, tracker)
    else:
        print("\nFailed to upload workout.")
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Garmin AI Coach")
    parser.add_argument("--config", "-c", default=None, help="Config file path")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # bot — run telegram bot
    subparsers.add_parser("bot", help="Run Telegram bot")

    # sync — one-shot data sync
    subparsers.add_parser("sync", help="Sync Garmin data")

    # morning — generate morning briefing
    morning_parser = subparsers.add_parser("morning", help="Morning briefing")
    morning_parser.add_argument("--dry-run", action="store_true", help="Print only, don't send")

    # analyze — analyze latest activity
    analyze_parser = subparsers.add_parser("analyze", help="Analyze latest activity")
    analyze_parser.add_argument("--dry-run", action="store_true", help="Print only, don't send")

    # reflect — self-reflection and proactive messaging
    reflect_parser = subparsers.add_parser("reflect", help="Self-reflect, update memory, send proactive messages")
    reflect_parser.add_argument("--dry-run", action="store_true", help="Print only, don't send")

    # impact — coach effectiveness report
    impact_parser = subparsers.add_parser("impact", help="Coach effectiveness report")
    impact_parser.add_argument("--days", type=int, default=30, help="Report period in days")

    # whoami — computed user model
    subparsers.add_parser("whoami", help="What the system knows about you")

    # strength-profile — computed strength intelligence
    strength_parser = subparsers.add_parser("strength-profile", help="Computed strength profile")
    strength_parser.add_argument("--days", type=int, default=90, help="Analysis window in days")

    # basketball-profile — computed basketball conditioning
    basketball_parser = subparsers.add_parser("basketball-profile", help="Computed basketball profile")
    basketball_parser.add_argument("--days", type=int, default=90, help="Analysis window in days")

    # insight — list/adopt/dismiss discovered insights
    insight_parser = subparsers.add_parser("insight", help="List/adopt/dismiss insights")
    insight_parser.add_argument("action", choices=["list", "adopt", "dismiss"])
    insight_parser.add_argument("key", nargs="?", default=None, help="Insight key (adopt/dismiss)")
    insight_parser.add_argument("--status", default=None, help="Filter for list")
    insight_parser.add_argument("--rule", default=None, help="Rule reference (adopt)")

    # concern — manage training concerns
    concern_parser = subparsers.add_parser("concern", help="Manage training concerns")
    concern_parser.add_argument("action", choices=["add", "list", "resolve"], help="Action")
    concern_parser.add_argument("text", nargs="?", default="", help="Concern text (for add)")
    concern_parser.add_argument("--impact", help="Impact description")
    concern_parser.add_argument("--sport", help="Affected sport")
    concern_parser.add_argument("--source", default="user", help="Source (user/riko/garmin-backend)")
    concern_parser.add_argument("--concern-id", help="Concern ID (for resolve)")

    # push-workout — upload workout plan to Garmin
    push_parser = subparsers.add_parser("push-workout", help="Push workout JSON to Garmin")
    push_parser.add_argument("plan", help="Workout plan as JSON string")

    # setup — interactive setup wizard
    subparsers.add_parser("setup", help="Interactive setup wizard for new users")

    args = parser.parse_args()

    # Setup doesn't need config — handle before build_components
    if args.command == "setup":
        from .setup import run_setup
        run_setup()
        return

    commands = {
        "bot": cmd_bot,
        "sync": cmd_sync,
        "morning": cmd_morning,
        "analyze": cmd_analyze,
        "reflect": cmd_reflect,
        "impact": cmd_impact,
        "whoami": cmd_whoami,
        "strength-profile": cmd_strength_profile,
        "basketball-profile": cmd_basketball_profile,
        "insight": cmd_insight,
        "concern": cmd_concern,
        "push-workout": cmd_push_workout,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
