from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage

from ..ai.charts import generate_chart
from ..ai.insights import ski_insights, gym_insights, recovery_insights, daily_summary

from ..ai.coach import AICoach
from ..garmin.sync import GarminSync
from ..garmin.workout import (
    upload_workout, update_workout, format_plan_text,
    load_workout_tracker, save_workout_tracker,
)

logger = logging.getLogger(__name__)

MAX_HISTORY = 20  # Keep last N messages to prevent token bloat


@dataclass
class CoachDeps:
    coach: AICoach
    sync: GarminSync
    pending_push: dict | None = None  # Set by push_workout tool
    pending_chart: bytes | None = None  # Set by show_chart tool


@dataclass
class ConversationState:
    """Per-chat conversation state."""
    history: list[ModelMessage] = field(default_factory=list)


# Global conversation states keyed by chat_id
_conversations: dict[str, ConversationState] = {}


def get_conversation(chat_id: str) -> ConversationState:
    if chat_id not in _conversations:
        _conversations[chat_id] = ConversationState()
    return _conversations[chat_id]


def _get_model() -> str:
    model = os.environ.get("COACH_AGENT_MODEL")
    if not model:
        raise RuntimeError("COACH_AGENT_MODEL not set. Configure llm.model in config.yaml.")
    if ":" not in model:
        model = f"openai:{model}"
    return model


coach_agent = Agent(
    _get_model(),
    deps_type=CoachDeps,
    instructions=(
        "You are a personal fitness coach with access to the user's Garmin health data. "
        "Respond in English. Be concise and direct.\n\n"
        "IMPORTANT RULES:\n"
        "- When the user asks about training, workouts, or what to do today: ALWAYS use generate_plan tool.\n"
        "- When the user asks about their health, recovery, or status: ALWAYS use show_status tool.\n"
        "- When the user shares personal info (gym, goals, injuries): ALWAYS use update_memory tool.\n"
        "- When the user asks about historical data or specific past info: use search_memory tool.\n"
        "- push_workout is ONLY for strength/gym workouts. For stretching, mobility, yoga, cardio: use generate_plan.\n"
        "- push_workout shows a preview first. The user must confirm before upload.\n"
        "- To answer questions about training progress, trends, or session analysis: use get_insights.\n"
        "- PROACTIVELY use show_chart when your response involves trends, multiple sessions, or numeric comparisons. "
        "Don't wait for the user to ask — if data is complex, a chart communicates better than text.\n"
        "- NEVER ignore the user's request. Match exactly what they asked for.\n"
        "- Only respond directly without tools for simple questions or casual chat."
    ),
)


@coach_agent.system_prompt
def inject_context(ctx: RunContext[CoachDeps]) -> str:
    metrics = ctx.deps.sync.db.get_daily_metrics()
    recent = ctx.deps.sync.db.get_recent_activities(days=7)

    # Inject compact summary, not full memory (use search_memory for details)
    memory_files = ctx.deps.coach.list_memory_files()

    metrics_str = "No data today"
    if metrics is not None:
        metrics_str = (
            f"HRV: {metrics.get('hrv_last_night', '?')}ms "
            f"(avg {metrics.get('hrv_weekly_avg', '?')}ms) | "
            f"Sleep: {_fmt_sleep(metrics.get('sleep_duration_min'))} | "
            f"BB: {metrics.get('body_battery_am', '?')} | "
            f"RHR: {metrics.get('resting_hr', '?')}"
        )

    recent_str = "\n".join(
        f"- {a['date']} {a['type']} ({a.get('duration_min', '?')}min)"
        for a in recent
    ) if recent else "None"

    # Read soul.md and profile.md directly (small, always relevant)
    soul = ctx.deps.coach.get_memory_file("soul")
    profile = ctx.deps.coach.get_memory_file("profile")

    return (
        f"## Coach Identity\n{soul}\n\n"
        f"## User Profile\n{profile}\n\n"
        f"## Today\n{metrics_str}\n\n"
        f"## Recent Activities\n{recent_str}\n\n"
        f"## Available Memory Files\n{', '.join(memory_files)}\n"
        f"(Use search_memory tool to look up details from any file)"
    )


# -- Tools --

@coach_agent.tool
def generate_plan(ctx: RunContext[CoachDeps], focus: str = "") -> str:
    """Generate a workout plan for today. Use when the user asks what to train, wants a plan, or asks for exercise suggestions."""
    ctx.deps.sync.sync_daily_metrics()
    return ctx.deps.coach.workout_plan(focus)


@coach_agent.tool
def push_workout(ctx: RunContext[CoachDeps], focus: str = "") -> str:
    """Generate a STRENGTH TRAINING workout preview for Garmin upload. Shows the plan first — user must say 'confirm' to actually upload. Only for strength/gym workouts."""
    non_strength = ["stretch", "yoga", "mobility", "cardio", "recovery", "warm up", "cool down"]
    if any(kw in focus.lower() for kw in non_strength):
        return f"Can't push '{focus}' to Garmin — only strength workouts are supported. Here's a text plan instead:\n\n" + ctx.deps.coach.workout_plan(focus)

    ctx.deps.sync.sync_daily_metrics()
    plan = ctx.deps.coach.workout_plan_structured(focus)
    if plan is None:
        return "Failed to generate structured plan."

    text = format_plan_text(plan)
    ctx.deps.pending_push = plan
    return f"{text}\nReady to upload. Confirm or tell me what to change."


@coach_agent.tool
def update_existing_workout(ctx: RunContext[CoachDeps], changes: str) -> str:
    """Modify an existing workout plan on Garmin. Use when the user wants to change exercises, weights, reps, or sets in a workout they already have."""
    tracker = load_workout_tracker(ctx.deps.sync.db.db_path.parent)
    if not tracker:
        return "No workouts to update. Ask me to create one first."

    matched_id, matched_plan = _find_workout(changes, tracker)
    if matched_plan is None:
        names = ", ".join(d.get("name", "?") for d in tracker.values())
        return f"Which workout? Available: {names}"

    updated = ctx.deps.coach.update_workout_plan(matched_plan, changes)
    if updated is None:
        return "Failed to parse update. Try again."

    success = update_workout(ctx.deps.sync.client, matched_id, updated)
    if success:
        tracker[matched_id] = updated
        save_workout_tracker(ctx.deps.sync.db.db_path.parent, tracker)
        return f"Updated!\n\n{format_plan_text(updated)}\nSync your watch."
    return "Garmin update failed."


@coach_agent.tool
def list_workouts(ctx: RunContext[CoachDeps]) -> str:
    """Show all workout plans currently uploaded to Garmin."""
    tracker = load_workout_tracker(ctx.deps.sync.db.db_path.parent)
    if not tracker:
        return "No workouts yet. Tell me to create one!"
    lines = []
    for data in tracker.values():
        name = data.get("name", "?")
        exercises = data.get("exercises", [])
        ex_list = [e.get("exercise", "?").replace("_", " ").title() for e in exercises]
        lines.append(f"{name}:\n  " + ", ".join(ex_list))
    return "\n\n".join(lines)


@coach_agent.tool
def show_status(ctx: RunContext[CoachDeps]) -> str:
    """Show today's health metrics and training readiness. Use when user asks about status, recovery, or readiness."""
    ctx.deps.sync.sync_daily_metrics()
    return recovery_insights(ctx.deps.sync.db)


@coach_agent.tool
def sync_data(ctx: RunContext[CoachDeps]) -> str:
    """Sync latest data from Garmin Connect."""
    metrics = ctx.deps.sync.sync_daily_metrics()
    new = ctx.deps.sync.sync_activities()
    msg = f"Synced. RHR: {metrics.get('resting_hr')} | HRV: {metrics.get('hrv_last_night')}ms"
    if new:
        msg += f"\n{len(new)} new activities"
    return msg


@coach_agent.tool
def get_insights(ctx: RunContext[CoachDeps], topic: str) -> str:
    """Get computed training insights with pre-calculated trends, statistics, and recommendations. Topics: 'ski' (speed trends, fatigue patterns), 'gym' (progressive overload, PRs), 'recovery' (HRV/sleep/readiness), 'all' (complete summary). All numbers are pre-computed by Python — no estimation needed."""
    db = ctx.deps.sync.db
    t = topic.lower()

    if any(kw in t for kw in ["ski", "snow", "board", "speed", "run", "season"]):
        return ski_insights(db)
    elif any(kw in t for kw in ["gym", "strength", "weight", "lift", "bench", "squat"]):
        return gym_insights(db)
    elif any(kw in t for kw in ["recovery", "hrv", "sleep", "ready", "status", "health"]):
        return recovery_insights(db)
    else:
        return daily_summary(db)


@coach_agent.tool
def search_memory(ctx: RunContext[CoachDeps], query: str) -> str:
    """Search memory files for specific information. Use when the user asks about historical data, past workouts, their profile details, gym equipment, or any stored info. The query should be keywords to search for."""
    memory_dir = ctx.deps.coach.memory_dir
    if not memory_dir.exists():
        return "No memory files."

    results = []
    query_lower = query.lower()
    keywords = query_lower.split()

    for md_file in sorted(memory_dir.glob("*.md")):
        content = md_file.read_text()
        lines = content.split("\n")
        matched_lines = []
        for i, line in enumerate(lines):
            if any(kw in line.lower() for kw in keywords):
                # Include surrounding context (1 line before, 2 after)
                start = max(0, i - 1)
                end = min(len(lines), i + 3)
                chunk = "\n".join(lines[start:end])
                if chunk not in matched_lines:
                    matched_lines.append(chunk)

        if matched_lines:
            results.append(f"**{md_file.stem}:**\n" + "\n---\n".join(matched_lines))

    # Also search workouts.json
    workouts_path = memory_dir / "workouts.json"
    if workouts_path.exists():
        workouts = json.loads(workouts_path.read_text())
        for wid, data in workouts.items():
            name = data.get("name", "")
            if any(kw in name.lower() for kw in keywords):
                results.append(f"**workout ({name}):**\n{json.dumps(data, indent=2)[:500]}")
            else:
                for ex in data.get("exercises", []):
                    if any(kw in ex.get("exercise", "").lower() for kw in keywords):
                        results.append(f"**workout ({name}):**\n{json.dumps(data, indent=2)[:500]}")
                        break

    return "\n\n".join(results) if results else f"No results for '{query}'"


@coach_agent.tool
def update_memory(ctx: RunContext[CoachDeps], info: str) -> str:
    """Save information to memory. Use when the user shares personal info, changes gym, reports an injury, or sets new goals."""
    return ctx.deps.coach.update_memory(info)


@coach_agent.tool
def show_chart(ctx: RunContext[CoachDeps], topic: str) -> str:
    """Generate a visual chart and send it as an image. Use when the user asks for a chart, graph, plot, trend visualization, or says 'show me'. Topics: 'ski' (speed trend), 'gym' (volume trend), 'recovery' (HRV/sleep/RHR), or 'all'."""
    chart_bytes, caption = generate_chart(ctx.deps.sync.db, topic)
    if chart_bytes is None:
        return "Not enough data to generate a chart yet. Need at least 2-3 sessions."
    ctx.deps.pending_chart = chart_bytes
    return f"[CHART:{caption}] Chart generated. Here's a summary of what it shows."


# -- Helpers --

def _find_workout(user_text: str, tracker: dict) -> tuple[str | None, dict | None]:
    user_lower = user_text.lower()
    for wid, data in tracker.items():
        name = data.get("name", "").lower()
        if name in user_lower:
            return wid, data
        name_words = name.split()
        if any(w in user_lower for w in name_words if len(w) > 3):
            return wid, data
    if len(tracker) == 1:
        wid = next(iter(tracker))
        return wid, tracker[wid]
    return None, None


def _fmt_sleep(minutes: int | None) -> str:
    if minutes is None:
        return "N/A"
    return f"{minutes // 60}h{minutes % 60:02d}m"
