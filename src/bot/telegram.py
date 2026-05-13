from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..ai.coach import AICoach
from ..garmin.sync import GarminSync

logger = logging.getLogger(__name__)

MAX_TELEGRAM_LENGTH = 4000
GARMIN_BACKEND_FRONT_DOOR_MESSAGE = (
    "Garmin backend is now data-only for training metrics.\n"
    "Please talk to Riko for coaching, training questions, and daily push follow-up."
)
TELEGRAM_SEND_ENABLED_ENV = "GARMIN_TELEGRAM_SEND_ENABLED"
LEGACY_TELEGRAM_SEND_ENABLED_ENV = "NEVE_TELEGRAM_SEND_ENABLED"


def _split_message(text: str, limit: int = MAX_TELEGRAM_LENGTH) -> list[str]:
    """Split long text at newline boundaries to avoid cutting mid-sentence."""
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Find last newline within limit
        split_at = text.rfind("\n", 0, limit)
        if split_at <= 0:
            # No newline found — fall back to hard split
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


class CoachBot:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        coach: AICoach,
        sync: GarminSync,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.coach = coach
        self.sync = sync
        self.app = Application.builder().token(bot_token).build()
        os.environ.setdefault("OPENAI_API_KEY", coach.client.api_key)
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

    def _is_authorized(self, update: Update) -> bool:
        return str(update.effective_chat.id) == self.chat_id

    async def _cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return
        await update.message.reply_text(GARMIN_BACKEND_FRONT_DOOR_MESSAGE)

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        await update.message.reply_text(GARMIN_BACKEND_FRONT_DOOR_MESSAGE)


    async def send_message(self, text: str) -> None:
        send_enabled = (
            os.environ.get(TELEGRAM_SEND_ENABLED_ENV) == "1"
            or os.environ.get(LEGACY_TELEGRAM_SEND_ENABLED_ENV) == "1"
        )
        if not send_enabled:
            logger.info(
                "Garmin backend Telegram send suppressed because Riko owns delivery (%s chars).",
                len(text),
            )
            return
        bot = self.app.bot
        if len(text) > 4000:
            for chunk in _split_message(text):
                await bot.send_message(chat_id=self.chat_id, text=chunk)
        else:
            await bot.send_message(chat_id=self.chat_id, text=text)

    def run(self) -> None:
        logger.info("Starting Telegram bot...")
        self.app.run_polling()
