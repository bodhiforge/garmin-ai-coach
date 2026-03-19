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
from .agent import coach_agent, CoachDeps, get_conversation, save_conversation, MAX_HISTORY

logger = logging.getLogger(__name__)

MAX_TELEGRAM_LENGTH = 4000


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
        self.deps = CoachDeps(coach=coach, sync=sync)
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
        await update.message.reply_text(
            "Garmin AI Coach\n\n"
            "Just talk to me naturally:\n"
            '- "What should I train today?"\n'
            '- "Push a pull day to my watch"\n'
            '- "Bump bench to 45kg"\n'
            '- "How\'s my recovery?"\n'
            '- "I switched to Anytime Fitness"\n'
            '- "What was my last leg workout?"'
        )

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return

        await update.message.chat.send_action("typing")
        user_text = update.message.text
        chat_id = str(update.effective_chat.id)
        conv = get_conversation(chat_id, db=self.sync.db)

        logger.info("Message: %s", user_text[:80])

        try:

            result = await coach_agent.run(
                user_text,
                deps=self.deps,
                message_history=conv.history,
            )

            # Update and persist conversation history
            conv.history = result.all_messages()[-MAX_HISTORY:]
            save_conversation(chat_id, conv.history, self.sync.db)

            response = result.output

            if len(response) > 4000:
                for chunk in _split_message(response):
                    await update.message.reply_text(chunk)
            else:
                await update.message.reply_text(response)

        except Exception as e:
            logger.error("Agent failed: %s", e, exc_info=True)
            await update.message.reply_text(
                "Something went wrong. Try again in a moment."
            )


    async def send_message(self, text: str) -> None:
        bot = self.app.bot
        if len(text) > 4000:
            for chunk in _split_message(text):
                await bot.send_message(chat_id=self.chat_id, text=chunk)
        else:
            await bot.send_message(chat_id=self.chat_id, text=text)

    def run(self) -> None:
        logger.info("Starting Telegram bot...")
        self.app.run_polling()
