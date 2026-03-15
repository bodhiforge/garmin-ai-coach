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


def cmd_reflect(args: argparse.Namespace) -> None:
    """Self-reflect: sync data, review, update memory, send proactive message."""
    config, _, _, sync, coach, bot = build_components(args.config)

    sync.sync_daily_metrics()
    sync.sync_activities()

    print("Reflecting...")
    message = coach.reflect()

    if message is not None:
        print(f"Proactive message: {message}")
        if not args.dry_run:
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

    args = parser.parse_args()

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
