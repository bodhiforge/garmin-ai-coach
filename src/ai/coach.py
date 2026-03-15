from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from openai import OpenAI

from ..db.models import Database

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class AICoach:
    def __init__(
        self, api_key: str, model: str, db: Database,
        base_url: str | None = None, data_dir: Path | None = None,
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.db = db
        self.memory_dir = (data_dir or Path("data")) / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._prompts: dict[str, str] = {}

    # -- Memory system --

    def get_memory(self) -> str:
        """Read all .md files from memory dir as combined context."""
        parts = []
        if not self.memory_dir.exists():
            return ""
        for md_file in sorted(self.memory_dir.glob("*.md")):
            parts.append(md_file.read_text().strip())
        return "\n\n---\n\n".join(parts)

    def get_memory_file(self, name: str) -> str:
        path = self.memory_dir / f"{name}.md"
        return path.read_text() if path.exists() else ""

    def save_memory_file(self, name: str, content: str) -> None:
        path = self.memory_dir / f"{name}.md"
        path.write_text(content)

    def list_memory_files(self) -> list[str]:
        if not self.memory_dir.exists():
            return []
        return [f.stem for f in sorted(self.memory_dir.glob("*.md"))]

    def update_memory(self, user_input: str) -> str:
        """AI decides which memory file to update based on user input."""
        current_files = self.list_memory_files()
        all_memory = self.get_memory()

        system = (
            "You manage a memory system for a fitness coaching AI. "
            "The memory consists of markdown files, each covering a topic.\n\n"
            f"Current files: {current_files}\n\n"
            f"Current memory:\n```\n{all_memory}\n```\n\n"
            "Based on the user's message, decide which file to update (or create a new one). "
            "Return your response in this EXACT format:\n"
            "FILE: <filename without .md>\n"
            "---\n"
            "<complete updated file content>\n\n"
            "Rules:\n"
            "- Merge new info, don't remove existing data unless user says to\n"
            "- If the info doesn't fit any existing file, create a new one\n"
            "- Return the COMPLETE file content, not just changes"
        )

        response = self._call_ai(system, user_input)

        # Parse FILE: header
        lines = response.strip().split("\n")
        filename = "profile"
        content_start = 0
        for i, line in enumerate(lines):
            if line.startswith("FILE:"):
                filename = line.split(":", 1)[1].strip().replace(".md", "")
                content_start = i + 1
                break

        # Find content after ---
        for i in range(content_start, len(lines)):
            if lines[i].strip() == "---":
                content_start = i + 1
                break

        content = "\n".join(lines[content_start:]).strip()
        # Strip markdown fences
        if content.startswith("```"):
            content = "\n".join(content.split("\n")[1:])
        if content.endswith("```"):
            content = "\n".join(content.split("\n")[:-1])
        content = content.strip()

        self.save_memory_file(filename, content)
        return f"Updated `{filename}.md`:\n\n{content}"

    def _load_prompt(self, name: str) -> str:
        if name not in self._prompts:
            prompt_path = PROMPTS_DIR / f"{name}.md"
            self._prompts[name] = prompt_path.read_text()
        return self._prompts[name]

    def _call_ai(self, system: str, user_message: str = "") -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message or "Please analyze."},
                ],
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("AI call failed: %s", e)
            return f"AI unavailable: {e}"

    def _memory_context(self) -> str:
        memory = self.get_memory()
        return f"\n## User Context (from memory)\n{memory}\n" if memory else ""

    def _sleep_accountability(self, recent_metrics: list[dict[str, Any]]) -> str:
        """Build sleep accountability context — how many nights violated 00:30 PST rule."""
        from datetime import datetime, timezone, timedelta
        violations = []
        PST = timezone(timedelta(hours=-8))
        for m in recent_metrics:
            sleep_start = m.get("sleep_start_time")  # ISO string or None
            if not sleep_start:
                continue
            try:
                dt = datetime.fromisoformat(str(sleep_start))
                dt_pst = dt.astimezone(PST)
                if dt_pst.hour > 0 or (dt_pst.hour == 0 and dt_pst.minute > 30):
                    violations.append(f"{m.get('date', '?')} — 睡眠时间 {dt_pst.strftime('%H:%M')} PST")
            except Exception:
                continue

        if not violations:
            return "本週睡眠達標 ✅ (全部在 00:30 前入睡)"
        count = len(violations)
        details = "\n".join(violations[-3:])
        return f"本週超標 {count} 次：\n{details}"

    def morning_briefing(self, metrics: dict[str, Any]) -> str:
        from .insights import daily_summary

        computed = daily_summary(self.db)

        prompt = self._load_prompt("morning").format(
            metrics=_format_metrics(metrics),
            computed_insights=computed,
        )
        return self._call_ai(prompt + self._memory_context())

    def post_gym_analysis(
        self, activity: dict[str, Any], sets: list[dict[str, Any]]
    ) -> str:
        from .insights import gym_insights, recovery_insights

        computed_gym = gym_insights(self.db)
        computed_recovery = recovery_insights(self.db)

        prompt = self._load_prompt("post_gym").format(
            session_summary=_format_activity_summary(activity),
            sets_data=_format_gym_sets(sets),
            computed_insights=f"{computed_gym}\n\n{computed_recovery}",
        )
        return self._call_ai(prompt)

    def post_ski_analysis(
        self, activity: dict[str, Any], runs: list[dict[str, Any]]
    ) -> str:
        from .insights import ski_insights

        computed = ski_insights(self.db)

        prompt = self._load_prompt("post_ski").format(
            session_summary=_format_activity_summary(activity),
            runs_data=_format_ski_runs(runs),
            computed_insights=computed,
        )
        return self._call_ai(prompt)

    def reflect(self) -> str | None:
        """Self-reflection: review data, update memory, return proactive message if any."""
        recent_activities = self.db.get_recent_activities(days=14)
        recent_metrics = self.db.get_recent_metrics(days=7)
        recent_gym = self._get_recent_gym_sets(days=14, limit=3)
        recent_ski = self._get_recent_ski_data(days=30)

        prompt = self._load_prompt("reflect").format(
            memory=self.get_memory(),
            recent_metrics=_format_metrics_list(recent_metrics),
            recent_activities=_format_activities(recent_activities),
            recent_gym_sets=recent_gym,
            recent_ski_data=recent_ski,
            today=str(date.today()),
        )

        response = self._call_ai(prompt)

        # Process memory updates
        proactive_message = self._process_reflection(response)
        return proactive_message

    def _process_reflection(self, response: str) -> str | None:
        lines = response.strip().split("\n")
        message = None
        in_memory = False
        in_message = False
        file_name = None
        file_lines = []

        for line in lines:
            if "### MEMORY UPDATES" in line:
                in_memory = True
                in_message = False
                continue
            if "### PROACTIVE MESSAGE" in line:
                # Save any pending file
                if file_name is not None and file_lines:
                    content = "\n".join(file_lines).strip()
                    if content and "NO UPDATES" not in content:
                        self.save_memory_file(file_name, content)
                        logger.info("Reflection updated memory: %s.md", file_name)
                in_memory = False
                in_message = True
                file_name = None
                file_lines = []
                continue

            if in_memory:
                if line.startswith("FILE:"):
                    # Save previous file if any
                    if file_name is not None and file_lines:
                        content = "\n".join(file_lines).strip()
                        if content:
                            self.save_memory_file(file_name, content)
                            logger.info("Reflection updated memory: %s.md", file_name)
                    file_name = line.split(":", 1)[1].strip().replace(".md", "")
                    file_lines = []
                elif line.strip() == "---":
                    continue
                elif line.strip() == "```":
                    continue
                elif "NO UPDATES" in line:
                    continue
                elif file_name is not None:
                    file_lines.append(line)

            if in_message:
                if "NO MESSAGE" in line:
                    continue
                if line.strip():
                    message = (message + "\n" + line) if message else line

        # Save last file if any
        if file_name is not None and file_lines:
            content = "\n".join(file_lines).strip()
            if content:
                self.save_memory_file(file_name, content)
                logger.info("Reflection updated memory: %s.md", file_name)

        return message.strip() if message else None

    def workout_plan(self, user_request: str = "") -> str:
        """Generate a text-based workout plan."""
        today_metrics = self.db.get_daily_metrics(None)
        recent_activities = self.db.get_recent_activities(days=14)
        recent_gym = self._get_recent_gym_sets(days=14, limit=3)

        prompt = self._load_prompt("workout_plan").format(
            today_metrics=_format_metrics(today_metrics) if today_metrics else "No data today",
            recent_activities=_format_activities(recent_activities),
            recent_gym_sets=recent_gym,
            user_request=user_request if user_request else "Generate the best workout for today",
        )
        return self._call_ai(prompt + self._memory_context())

    def workout_plan_structured(self, user_request: str = "") -> dict | None:
        """Generate a structured workout plan (JSON) for Garmin upload."""
        today_metrics = self.db.get_daily_metrics(None)
        recent_activities = self.db.get_recent_activities(days=14)
        recent_gym = self._get_recent_gym_sets(days=14, limit=3)

        # Load exercise list for the prompt
        exercises_path = self.memory_dir.parent / "exercises.json"
        exercise_list = "Use standard Garmin exercise names"
        if exercises_path.exists():
            exercises = json.loads(exercises_path.read_text())
            # Build compact list for prompt
            ex_lines = []
            for cat, exs in exercises.items():
                ex_names = list(exs.keys())[:10]  # Limit per category
                ex_lines.append(f"{cat}: {', '.join(ex_names)}")
            exercise_list = "\n".join(ex_lines)

        prompt = self._load_prompt("workout_structured").format(
            memory=self.get_memory(),
            today_metrics=_format_metrics(today_metrics) if today_metrics else "No data today",
            recent_activities=_format_activities(recent_activities),
            recent_gym_sets=recent_gym,
            user_request=user_request if user_request else "Generate the best workout for today",
            exercise_list=exercise_list,
        )

        response = self._call_ai(prompt)

        # Parse JSON from response
        try:
            # Strip markdown code fences
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[:-1])
            return json.loads(cleaned.strip())
        except json.JSONDecodeError as e:
            logger.error("Failed to parse workout JSON: %s\nResponse: %s", e, response[:200])
            return None

    def update_workout_plan(self, current_plan: dict, user_feedback: str) -> dict | None:
        """Update a workout plan based on user feedback."""
        prompt = self._load_prompt("post_workout_update").format(
            current_plan=json.dumps(current_plan, indent=2),
            user_feedback=user_feedback,
        )

        response = self._call_ai(prompt)

        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[:-1])
            return json.loads(cleaned.strip())
        except json.JSONDecodeError as e:
            logger.error("Failed to parse updated workout: %s", e)
            return None

    def _get_recent_gym_sets(self, days: int = 14, limit: int = 3) -> str:
        activities = self.db.get_recent_activities(days=days, activity_type="strength")
        if not activities:
            return "No recent gym data"

        lines = []
        for a in activities[:limit]:
            sets = self.db.get_gym_sets(a["id"])
            if sets:
                lines.append(f"\n{a['date']} ({a.get('duration_min', '?')}min):")
                for s in sets:
                    lines.append(
                        f"  {s.get('exercise', '?')} | "
                        f"{s.get('reps', '?')} reps × {s.get('weight_kg', '?')}kg | "
                        f"peak HR {s.get('peak_hr', '?')}"
                    )
        return "\n".join(lines) if lines else "No set data available"

    def _get_recent_ski_data(self, days: int = 30) -> str:
        activities = self.db.get_recent_activities(days=days, activity_type="skiing")
        if not activities:
            return "No recent ski data"

        lines = []
        for a in activities:
            runs = self.db.get_ski_runs(a["id"])
            if not runs:
                continue
            speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
            drops = [r.get("vertical_drop_m", 0) or 0 for r in runs]
            max_speed = max(speeds) if speeds else 0
            total_drop = sum(drops)

            lines.append(
                f"\n{a['date']} | {len(runs)} runs | "
                f"max {max_speed:.1f}km/h | total drop {total_drop:.0f}m | "
                f"{a.get('duration_min', '?')}min"
            )
            for r in runs:
                lines.append(
                    f"  Run {r['run_number']}: "
                    f"{r.get('max_speed_kmh', '?')}km/h | "
                    f"drop {r.get('vertical_drop_m', '?')}m | "
                    f"max HR {r.get('max_hr', '?')} | "
                    f"lift HR {r.get('lift_top_hr', '?')}"
                )
        return "\n".join(lines) if lines else "No ski run data"

    def chat(self, user_message: str) -> str:
        today_metrics = self.db.get_daily_metrics(None)
        recent_activities = self.db.get_recent_activities(days=7)
        recent_metrics = self.db.get_recent_metrics(days=7)

        # Save user message to history
        self.db.add_chat_message("user", user_message)

        system = self._load_prompt("chat").format(
            current_metrics=_format_metrics(today_metrics) if today_metrics else "No data today",
            recent_activities=_format_activities(recent_activities),
            recent_metrics=_format_metrics_list(recent_metrics),
        ) + self._memory_context()

        # Include recent chat history for context
        chat_history = self.db.get_recent_chat(limit=10)
        messages = [
            {"role": msg["role"], "content": msg["message"]}
            for msg in chat_history
        ]
        # Ensure messages alternate properly and start with user
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": user_message})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "system", "content": system}, *messages],
            )
            reply = response.choices[0].message.content
        except Exception as e:
            logger.error("Chat AI call failed: %s", e)
            reply = f"AI 暂时不可用: {e}"

        self.db.add_chat_message("assistant", reply)
        return reply


def _format_metrics(metrics: dict[str, Any] | None) -> str:
    if metrics is None:
        return "No data available"

    lines = [
        f"Date: {metrics.get('date', 'N/A')}",
        f"HRV (last night): {metrics.get('hrv_last_night', 'N/A')} ms",
        f"HRV (weekly avg): {metrics.get('hrv_weekly_avg', 'N/A')} ms",
        f"Sleep: {_format_sleep_duration(metrics.get('sleep_duration_min'))}",
        f"Sleep Score: {metrics.get('sleep_score', 'N/A')}/100",
        f"Body Battery (AM): {metrics.get('body_battery_am', 'N/A')}/100",
        f"Stress (avg): {metrics.get('stress_avg', 'N/A')}",
        f"Resting HR: {metrics.get('resting_hr', 'N/A')} bpm",
        f"SpO2: {metrics.get('spo2_avg', 'N/A')}%",
    ]
    return "\n".join(lines)


def _format_sleep_duration(minutes: int | None) -> str:
    if minutes is None:
        return "N/A"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins:02d}m"


def _format_activities(activities: list[dict[str, Any]]) -> str:
    if not activities:
        return "No recent activities"

    lines = []
    for a in activities:
        duration = a.get("duration_min", "?")
        avg_hr = a.get("avg_hr", "?")
        lines.append(
            f"- {a['date']} | {a['type']} | {duration}min | avg HR {avg_hr} | {a.get('calories', '?')} cal"
        )
    return "\n".join(lines)


def _format_metrics_list(metrics_list: list[dict[str, Any]]) -> str:
    if not metrics_list:
        return "No recent data"

    lines = []
    for m in metrics_list:
        lines.append(
            f"- {m['date']} | HRV {m.get('hrv_last_night', '?')}ms | "
            f"Sleep {_format_sleep_duration(m.get('sleep_duration_min'))} | "
            f"BB {m.get('body_battery_am', '?')} | "
            f"RHR {m.get('resting_hr', '?')}"
        )
    return "\n".join(lines)


def _format_activity_summary(activity: dict[str, Any]) -> str:
    return (
        f"Type: {activity.get('type', 'N/A')}\n"
        f"Date: {activity.get('date', 'N/A')}\n"
        f"Duration: {activity.get('duration_min', 'N/A')} min\n"
        f"Avg HR: {activity.get('avg_hr', 'N/A')} bpm\n"
        f"Max HR: {activity.get('max_hr', 'N/A')} bpm\n"
        f"Calories: {activity.get('calories', 'N/A')}"
    )


def _format_gym_sets(sets: list[dict[str, Any]]) -> str:
    if not sets:
        return "No set data available"

    lines = []
    for s in sets:
        lines.append(
            f"Set {s['set_number']}: {s.get('exercise', '?')} | "
            f"{s.get('reps', '?')} reps × {s.get('weight_kg', '?')}kg | "
            f"peak HR {s.get('peak_hr', '?')} | "
            f"recovery HR {s.get('recovery_hr', '?')}"
        )
    return "\n".join(lines)


def _format_ski_runs(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "No run data available"

    lines = []
    for r in runs:
        lines.append(
            f"Run {r['run_number']}: "
            f"max {r.get('max_speed_kmh', '?')}km/h | "
            f"avg {r.get('avg_speed_kmh', '?')}km/h | "
            f"drop {r.get('vertical_drop_m', '?')}m | "
            f"{r.get('duration_sec', '?')}s | "
            f"max HR {r.get('max_hr', '?')} | "
            f"lift top HR {r.get('lift_top_hr', '?')}"
        )
    return "\n".join(lines)
