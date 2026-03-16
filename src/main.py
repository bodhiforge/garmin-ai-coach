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
    """One-shot sync of Garmin data."""
    _, _, _, sync, _, _ = build_components(args.config)

    metrics = sync.sync_daily_metrics()
    print(f"Daily metrics synced: HRV={metrics.get('hrv_last_night')}ms, "
          f"RHR={metrics.get('resting_hr')}bpm")

    new_activities = sync.sync_activities()
    print(f"Activities synced: {len(new_activities)} new")


def cmd_morning(args: argparse.Namespace) -> None:
    """Generate and send morning briefing."""
    config, _, _, sync, coach, bot = build_components(args.config)

    metrics = sync.sync_daily_metrics()
    briefing = coach.morning_briefing(metrics)

    print(briefing)

    if not args.dry_run:
        asyncio.run(bot.send_message(briefing))
        print("\nSent to Telegram.")


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


def _build_activity_analysis(
    events: list[str], db: Database, coach: AICoach,
) -> str | None:
    """If events include a new activity, return full analysis. Otherwise None."""
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

    # Check for new achievements
    from .ai.gamification import check_achievements
    new_achievements = check_achievements(sync.db, sync.data_dir)
    for ach in new_achievements:
        msg = f"🏆 Achievement Unlocked: {ach['name']}!\n{ach['description']}"
        print(msg)
        if not dry_run:
            asyncio.run(bot.send_message(msg))

    # Event-driven notification (Python decides, LLM writes copy)
    from .ai.notify import should_notify
    should_send, events, score = should_notify(sync.db)
    print(f"Events: {events} | Score: {score} | Send: {should_send}")

    if should_send:
        # Check if there's a new activity — send full analysis instead of generic notification
        message = _build_activity_analysis(events, sync.db, coach)
        if message is None:
            # No activity event — fall back to generic LLM notification for alerts
            event_summary = "; ".join(events)
            message = coach._call_ai(
                "You are a concise fitness coach. Write a short Telegram notification (2-3 sentences) "
                "about these events. Be direct, reference specific numbers. Do not add generic advice.",
                event_summary,
            )
        print(f"Message: {message}")
        if not dry_run:
            # Record each event type
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
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
