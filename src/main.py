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


def cmd_sync(args: argparse.Namespace) -> None:
    """One-shot sync of Garmin data. Auto-sends morning brief on wake-up detection."""
    config, _, _, sync, coach, bot = build_components(args.config)

    metrics = sync.sync_daily_metrics()
    print(f"Daily metrics synced: HRV={metrics.get('hrv_last_night')}ms, "
          f"RHR={metrics.get('resting_hr')}bpm")

    new_activities = sync.sync_activities()
    print(f"Activities synced: {len(new_activities)} new")

    # Auto-push AI morning brief when wake-up detected
    if getattr(sync, '_last_wake_detected', False):
        import asyncio
        briefing = coach.morning_briefing(metrics)
        asyncio.run(bot.send_message(briefing))
        print("Wake-up detected — AI morning brief sent to Telegram.")


def cmd_morning(args: argparse.Namespace) -> None:
    """Sync data + write training digest to file. No LLM call.
    The digest is consumed by Riko's OpenClaw cron which uses Opus."""
    from datetime import date, timedelta
    from pathlib import Path
    from .ai.insights import daily_summary

    config, _, _, sync, coach, bot = build_components(args.config)

    metrics = sync.sync_daily_metrics()
    sync.sync_activities()

    # If today has no sleep data, use yesterday's
    if metrics.get("sleep_duration_min") is None:
        yesterday = date.today() - timedelta(days=1)
        metrics = sync.client.get_daily_metrics(yesterday)

    # Computed insights (pure Python, no LLM)
    computed = daily_summary(coach.db)

    # Format raw metrics
    from .ai.coach import _format_metrics
    raw_metrics = _format_metrics(metrics)

    # Read fitness plan
    plan_path = Path.home() / "ai" / "data" / "fitness-plan.md"
    fitness_plan = plan_path.read_text().strip() if plan_path.exists() else ""

    # Read user memory context
    memory_context = coach.get_memory()

    # Write digest
    digest_path = Path.home() / "ai" / "data" / "signals" / "training-digest.txt"
    digest_path.parent.mkdir(parents=True, exist_ok=True)

    digest = f"""# Training Digest — {date.today()} ({date.today().strftime('%A')})
# Generated: {date.today().isoformat()} by Neve (pure data, no LLM)
# Consumed by: Riko OpenClaw morning cron → Opus → Telegram

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

    if args.dry_run:
        print("\n--- DIGEST PREVIEW ---")
        print(digest[:2000])
        if len(digest) > 2000:
            print(f"\n... ({len(digest) - 2000} more chars)")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze the most recent activity."""
    _, db, _, sync, coach, bot = build_components(args.config)

    # Sync first to get latest
    new_activities = sync.sync_activities()
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

    from .ai.impact import impact_report
    report = impact_report(db, days=args.days)
    print(report)


def cmd_whoami(args: argparse.Namespace) -> None:
    """Show computed user model — what the system knows about you."""
    _, db, _, sync, _, _ = build_components(args.config)

    sync.sync_daily_metrics()
    sync.sync_activities()

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

    # concern — manage training concerns
    concern_parser = subparsers.add_parser("concern", help="Manage training concerns")
    concern_parser.add_argument("action", choices=["add", "list", "resolve"], help="Action")
    concern_parser.add_argument("text", nargs="?", default="", help="Concern text (for add)")
    concern_parser.add_argument("--impact", help="Impact description")
    concern_parser.add_argument("--sport", help="Affected sport")
    concern_parser.add_argument("--source", default="user", help="Source (user/riko/neve)")
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
        "concern": cmd_concern,
        "push-workout": cmd_push_workout,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
