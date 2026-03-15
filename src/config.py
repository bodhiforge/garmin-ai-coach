from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class GarminConfig:
    email: str
    password: str


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str = "gpt-4o-mini"
    base_url: str | None = None  # None = OpenAI default; set for other providers


@dataclass(frozen=True)
class HRTarget:
    strength: int = 20
    hypertrophy: int = 40
    endurance: int = 50


@dataclass(frozen=True)
class CoachConfig:
    morning_push_hour: int = 7
    sync_interval_min: int = 30
    resting_hr_override: int | None = None
    hr_target: HRTarget = field(default_factory=HRTarget)


@dataclass(frozen=True)
class AppConfig:
    garmin: GarminConfig
    telegram: TelegramConfig
    llm: LLMConfig
    coach: CoachConfig = field(default_factory=CoachConfig)
    data_dir: Path = field(default_factory=lambda: Path("data"))


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        path = os.environ.get("GARMIN_COACH_CONFIG", "config.yaml")

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    hr_target_raw = raw.get("coach", {}).get("hr_target", {})
    coach_raw = raw.get("coach", {})

    return AppConfig(
        garmin=GarminConfig(**raw["garmin"]),
        telegram=TelegramConfig(**raw["telegram"]),
        llm=LLMConfig(**raw["llm"]),
        coach=CoachConfig(
            morning_push_hour=coach_raw.get("morning_push_hour", 7),
            sync_interval_min=coach_raw.get("sync_interval_min", 30),
            resting_hr_override=coach_raw.get("resting_hr_override"),
            hr_target=HRTarget(**hr_target_raw) if hr_target_raw else HRTarget(),
        ),
        data_dir=Path(raw.get("data_dir", "data")),
    )
