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
        conv = get_conversation(chat_id)

        logger.info("Message: %s", user_text[:80])

        # Handle pending push — let AI classify intent
        if self.deps.pending_push is not None:
            intent = await self._classify_push_intent(user_text)
            if intent == "confirm":
                await self._confirm_push(update)
                return
            elif intent == "cancel":
                self.deps.pending_push = None
                await update.message.reply_text("Cancelled.")
                return
            else:
                # "change" — modify pending plan inline, stay in push flow
                await self._change_pending(update, user_text)
                return

        try:

            result = await coach_agent.run(
                user_text,
                deps=self.deps,
                message_history=conv.history,
            )

            # Update conversation history
            conv.history = result.all_messages()[-MAX_HISTORY:]

            response = result.output

            # Send chart if generated
            chart_bytes = self.deps.pending_chart
            self.deps.pending_chart = None
            if chart_bytes:
                await update.message.reply_photo(photo=chart_bytes, caption=response[:1024])
            elif len(response) > 4000:
                for chunk in _split_message(response):
                    await update.message.reply_text(chunk)
            else:
                await update.message.reply_text(response)

        except Exception as e:
            logger.error("Agent failed: %s", e, exc_info=True)
            await update.message.reply_text(
                "Something went wrong. Try again in a moment."
            )

    async def _classify_push_intent(self, text: str) -> str:
        """Use LLM to classify: confirm, cancel, or change."""
        response = self.coach.client.chat.completions.create(
            model=self.coach.model,
            max_tokens=10,
            messages=[
                {"role": "system", "content": "A workout plan was just shown to the user. Classify their response: confirm, cancel, or change."},
                {"role": "user", "content": text},
            ],
        )
        intent = response.choices[0].message.content.strip().lower()
        if "confirm" in intent or "upload" in intent or "yes" in intent:
            return "confirm"
        elif "cancel" in intent or "no" in intent:
            return "cancel"
        return "change"

    async def _change_pending(self, update: Update, user_text: str) -> None:
        """Modify pending workout plan based on user feedback."""
        await update.message.chat.send_action("typing")
        updated = self.coach.update_workout_plan(self.deps.pending_push, user_text)
        if updated is not None:
            self.deps.pending_push = updated
            text = format_plan_text(updated)
            await update.message.reply_text(
                f"{text}\nUpdated. Confirm or tell me what else to change."
            )
        else:
            await update.message.reply_text(
                "Couldn't parse that change. Try again or say 'cancel'."
            )

    async def _confirm_push(self, update: Update) -> None:
        plan = self.deps.pending_push
        self.deps.pending_push = None

        if plan is None:
            await update.message.reply_text("No pending plan found. Try pushing again.")
            return

        await update.message.chat.send_action("typing")
        logger.info("Confirming push: %s (%d exercises)", plan.get("name", "?"), len(plan.get("exercises", [])))

        try:
            logger.info("Plan to upload: %s", json.dumps(plan, default=str)[:200])
            result = upload_workout(self.sync.client, plan)
            logger.info("Upload result: %s", result)
            if result is not None:
                tracker = load_workout_tracker(self.sync.db.db_path.parent)
                tracker[result] = plan
                save_workout_tracker(self.sync.db.db_path.parent, tracker)
                await update.message.reply_text(
                    f"Uploaded '{plan.get('name', 'workout')}' to Garmin! Sync your watch."
                )
            else:
                logger.error("Upload returned None for plan: %s", plan.get("name"))
                await update.message.reply_text("Upload failed. Try again.")
        except Exception as e:
            logger.error("Push confirm failed: %s", e, exc_info=True)
            await update.message.reply_text(
                "Upload to Garmin failed. Try again in a moment."
            )

    async def send_message(self, text: str) -> None:
        bot = self.app.bot
        if len(text) > 4000:
            for chunk in _split_message(text):
                await bot.send_message(chat_id=self.chat_id, text=chunk)
        else:
            await bot.send_message(chat_id=self.chat_id, text=text)

    async def send_photo(self, photo_bytes: bytes, caption: str = "") -> None:
        bot = self.app.bot
        await bot.send_photo(
            chat_id=self.chat_id,
            photo=photo_bytes,
            caption=caption[:1024] if caption else None,
        )

    def run(self) -> None:
        logger.info("Starting Telegram bot...")
        self.app.run_polling()
