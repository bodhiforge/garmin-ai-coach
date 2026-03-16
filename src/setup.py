"""Interactive setup wizard for new users."""

from __future__ import annotations

import asyncio
import getpass
import logging
import sys
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# LLM provider presets
LLM_PROVIDERS = {
    "1": {
        "name": "OpenAI",
        "model": "gpt-4o",
        "base_url": None,
        "key_url": "https://platform.openai.com/api-keys",
    },
    "2": {
        "name": "Gemini",
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_url": "https://aistudio.google.com/apikey",
    },
    "3": {
        "name": "OpenRouter",
        "model": "google/gemini-2.0-flash-001",
        "base_url": "https://openrouter.ai/api/v1",
        "key_url": "https://openrouter.ai/keys",
    },
}


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    result = input(f"{prompt}{suffix}: ").strip()
    return result if result else default


def _ask_password(prompt: str) -> str:
    return getpass.getpass(f"{prompt}: ")


def _print_step(n: int, title: str) -> None:
    print(f"\n{'='*50}")
    print(f"  Step {n}: {title}")
    print(f"{'='*50}\n")


def _verify_garmin(email: str, password: str) -> bool:
    print("  Verifying Garmin login...", end=" ", flush=True)
    try:
        from garminconnect import Garmin
        client = Garmin(email, password)
        client.login()
        # Save session for later use
        garth_home = Path.home() / ".garth"
        client.garth.dump(str(garth_home))
        print(f"OK ({client.display_name})")
        return True
    except Exception as e:
        print(f"FAILED")
        print(f"  Error: {e}")
        return False


def _verify_llm(api_key: str, model: str, base_url: str | None) -> bool:
    print("  Verifying LLM connection...", end=" ", flush=True)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": "Say hi"}],
        )
        print(f"OK ({model})")
        return True
    except Exception as e:
        print(f"FAILED")
        print(f"  Error: {e}")
        return False


def _detect_chat_id(bot_token: str) -> str | None:
    """Start a temporary bot to detect the user's chat_id."""
    print("  Detecting your chat ID...")
    print("  >>> Send any message to your bot in Telegram now <<<")
    print("  Waiting", end="", flush=True)

    try:
        import telegram

        async def _poll_for_message() -> str | None:
            from telegram import Bot
            bot = Bot(token=bot_token)

            # Clear any old updates
            updates = await bot.get_updates(timeout=1)
            offset = updates[-1].update_id + 1 if updates else None

            # Poll for new message (up to 60 seconds)
            for _ in range(12):
                print(".", end="", flush=True)
                updates = await bot.get_updates(offset=offset, timeout=5)
                for update in updates:
                    if update.message and update.message.chat:
                        chat_id = str(update.message.chat.id)
                        user = update.message.from_user
                        name = user.first_name if user else "Unknown"
                        print(f" Got it!")
                        print(f"  Chat ID: {chat_id} (from {name})")
                        return chat_id
            print(" Timed out.")
            return None

        return asyncio.run(_poll_for_message())
    except Exception as e:
        print(f" Error: {e}")
        return None


def _verify_bot_token(bot_token: str) -> bool:
    print("  Verifying bot token...", end=" ", flush=True)
    try:
        from telegram import Bot
        bot = Bot(token=bot_token)

        async def _check():
            me = await bot.get_me()
            return me.username

        username = asyncio.run(_check())
        print(f"OK (@{username})")
        return True
    except Exception as e:
        print(f"FAILED")
        print(f"  Error: {e}")
        return False


def _collect_profile() -> str:
    """Ask basic profile questions and generate profile.md content."""
    print("  Let's set up your training profile.\n")

    sports = _ask("What sports do you do? (e.g., gym, skiing, running)", "gym")
    goals = _ask("Training goals? (e.g., build muscle, lose weight, get faster)")
    injuries = _ask("Any injuries or limitations? (press Enter to skip)")
    experience = _ask("Experience level? (beginner/intermediate/advanced)", "intermediate")

    lines = [
        "# Profile",
        "",
        f"## Sports: {sports}",
        f"## Experience: {experience}",
        f"## Goals: {goals}",
    ]
    if injuries:
        lines.append(f"## Injuries/Limitations: {injuries}")

    return "\n".join(lines)


def run_setup() -> None:
    """Interactive setup wizard."""
    print("\n" + "=" * 50)
    print("  Garmin AI Coach — Setup Wizard")
    print("=" * 50)

    config_path = Path("config.yaml")
    if config_path.exists():
        overwrite = _ask("\nconfig.yaml already exists. Overwrite? (y/N)", "N")
        if overwrite.lower() != "y":
            print("Setup cancelled.")
            return

    # Step 1: Garmin
    _print_step(1, "Garmin Connect")
    print("  Your Garmin email and password are needed to sync health data.\n")

    garmin_email = ""
    garmin_password = ""
    while True:
        garmin_email = _ask("Garmin email")
        garmin_password = _ask_password("Garmin password")
        if _verify_garmin(garmin_email, garmin_password):
            break
        retry = _ask("Try again? (Y/n)", "Y")
        if retry.lower() == "n":
            print("  Skipping Garmin verification. You can fix config.yaml later.")
            break

    # Step 2: Telegram Bot
    _print_step(2, "Telegram Bot")
    print("  You need a Telegram bot to receive coaching messages.\n")
    print("  If you don't have one yet:")
    print("    1. Open Telegram and message @BotFather")
    print("    2. Send /newbot and follow the prompts")
    print("    3. Copy the bot token\n")

    bot_token = ""
    while True:
        bot_token = _ask("Bot token")
        if _verify_bot_token(bot_token):
            break
        retry = _ask("Try again? (Y/n)", "Y")
        if retry.lower() == "n":
            print("  Skipping verification.")
            break

    # Step 3: Chat ID (auto-detect)
    _print_step(3, "Your Chat ID")

    chat_id = ""
    if bot_token:
        auto = _ask("Auto-detect chat ID? (Y/n)", "Y")
        if auto.lower() != "n":
            chat_id = _detect_chat_id(bot_token) or ""

    if not chat_id:
        chat_id = _ask("Enter your Telegram chat ID manually")

    # Step 4: LLM Provider
    _print_step(4, "LLM Provider")
    print("  Choose your AI provider:\n")
    for key, provider in LLM_PROVIDERS.items():
        print(f"    {key}) {provider['name']} — {provider['model']}")
    print(f"    4) Custom (any OpenAI-compatible API)")
    print()

    choice = _ask("Choice", "1")

    if choice in LLM_PROVIDERS:
        provider = LLM_PROVIDERS[choice]
        llm_model = provider["model"]
        llm_base_url = provider["base_url"]
        print(f"\n  Get your API key at: {provider['key_url']}\n")
    else:
        llm_model = _ask("Model name")
        llm_base_url = _ask("Base URL (e.g., http://localhost:11434/v1)")

    llm_api_key = ""
    while True:
        llm_api_key = _ask_password("API key")
        if _verify_llm(llm_api_key, llm_model, llm_base_url):
            break
        retry = _ask("Try again? (Y/n)", "Y")
        if retry.lower() == "n":
            print("  Skipping verification.")
            break

    # Step 5: Profile
    _print_step(5, "Training Profile")
    profile_content = _collect_profile()

    # Write config.yaml
    config = {
        "garmin": {
            "email": garmin_email,
            "password": garmin_password,
        },
        "telegram": {
            "bot_token": bot_token,
            "chat_id": chat_id,
        },
        "llm": {
            "api_key": llm_api_key,
            "model": llm_model,
        },
        "coach": {
            "morning_push_hour": 7,
            "sync_interval_min": 30,
        },
    }
    if llm_base_url:
        config["llm"]["base_url"] = llm_base_url

    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    print(f"\n  Saved config.yaml")

    # Write profile.md
    data_dir = Path("data")
    memory_dir = data_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    profile_path = memory_dir / "profile.md"
    if not profile_path.exists() or _ask("Overwrite existing profile.md? (y/N)", "N").lower() == "y":
        profile_path.write_text(profile_content)
        print(f"  Saved data/memory/profile.md")

    # Write default soul.md if missing
    soul_path = memory_dir / "soul.md"
    if not soul_path.exists():
        soul_path.write_text(
            "# Coach Personality\n\n"
            "You are a direct, data-driven fitness coach. "
            "Reference specific numbers from the user's data. "
            "Keep messages concise — they're read on a phone. "
            "Be encouraging but honest about areas for improvement."
        )
        print(f"  Saved data/memory/soul.md")

    # Done
    print(f"\n{'='*50}")
    print("  Setup complete!")
    print(f"{'='*50}\n")
    print("  Next steps:")
    print("    1. Sync your Garmin data:  python -m src.main sync")
    print("    2. Start the bot:          python -m src.main bot")
    print("    3. Message your bot in Telegram!")
    print()
