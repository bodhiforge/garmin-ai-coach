from __future__ import annotations

import json
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
from ..garmin.workout import (
    upload_workout, format_plan_text,
    load_workout_tracker, save_workout_tracker,
)
from .agent import coach_agent, CoachDeps, get_conversation, MAX_HISTORY

logger = logging.getLogger(__name__)


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
        conv = get_conversation(chat_id)

        logger.info("Message: %s", user_text[:80])

        # Handle pending push — let AI classify intent
        if conv.pending_push is not None:
            intent = await self._classify_push_intent(user_text)
            if intent == "confirm":
                await self._confirm_push(update, conv)
                return
            elif intent == "cancel":
                conv.pending_push = None
                await update.message.reply_text("Cancelled.")
                return
            # "change" → falls through to agent.run below

        try:
            result = await coach_agent.run(
                user_text,
                deps=self.deps,
                message_history=conv.history,
            )

            # Update conversation history
            conv.history = result.all_messages()[-MAX_HISTORY:]

            response = result.output

            # Check for pending push marker
            if "PENDING_PUSH:" in response:
                marker_line = [l for l in response.split("\n") if l.startswith("PENDING_PUSH:")][0]
                plan_json = marker_line.replace("PENDING_PUSH:", "")
                conv.pending_push = json.loads(plan_json)
                # Remove marker from display
                response = response.replace(marker_line + "\n", "")

            # Send response (respect Telegram 4096 limit)
            if len(response) > 4000:
                for i in range(0, len(response), 4000):
                    await update.message.reply_text(response[i:i+4000])
            else:
                await update.message.reply_text(response)

        except Exception as e:
            logger.error("Agent failed: %s", e)
            await update.message.reply_text(f"Error: {e}")

    async def _classify_push_intent(self, text: str) -> str:
        """Use LLM to classify: confirm, cancel, or change."""
        response = self.coach.client.chat.completions.create(
            model=self.coach.model,
            max_tokens=10,
            messages=[
                {"role": "system", "content": "Classify the user's intent. A workout plan was just shown. Reply with exactly one word: confirm, cancel, or change."},
                {"role": "user", "content": text},
            ],
        )
        intent = response.choices[0].message.content.strip().lower()
        if "confirm" in intent or "upload" in intent or "yes" in intent:
            return "confirm"
        elif "cancel" in intent or "no" in intent:
            return "cancel"
        return "change"

    async def _confirm_push(self, update: Update, conv) -> None:
        plan = conv.pending_push
        conv.pending_push = None

        await update.message.chat.send_action("typing")

        try:
            result = upload_workout(self.sync.client, plan)
            if result is not None:
                tracker = load_workout_tracker(self.sync.db.db_path.parent)
                tracker[result] = plan
                save_workout_tracker(self.sync.db.db_path.parent, tracker)
                await update.message.reply_text(
                    f"Uploaded '{plan.get('name', 'workout')}' to Garmin! Sync your watch."
                )
            else:
                await update.message.reply_text("Upload failed. Try again.")
        except Exception as e:
            logger.error("Push confirm failed: %s", e)
            await update.message.reply_text(f"Upload error: {e}")

    async def send_message(self, text: str) -> None:
        bot = self.app.bot
        await bot.send_message(chat_id=self.chat_id, text=text)

    def run(self) -> None:
        logger.info("Starting Telegram bot...")
        self.app.run_polling()
