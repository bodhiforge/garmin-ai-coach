# Garmin AI Coach

AI training coach that syncs your Garmin data, analyzes it with Python, and coaches you via Telegram. Not a dashboard — a coach that remembers, learns patterns, holds you accountable, and sends charts.

**Core idea:** Python does the math. LLM does the talking. Zero hallucinated numbers.

```
Garmin Watch → Garmin Connect API → Python (compute insights) → LLM (present) → Telegram
```

## Quick Start

```bash
git clone https://github.com/bodhiforge/garmin-ai-coach.git
cd garmin-ai-coach
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
garmin-onboard
```

The wizard validates your Garmin login, creates a Telegram bot, picks an LLM provider, and sets up your profile. 5 minutes.

## What It Does

### Talk to it on Telegram

Just chat naturally. The bot has 11 tools it calls based on what you say:

| You say | Bot does |
|---------|----------|
| "What should I train today?" | Generates plan based on HRV, sleep, recent sessions |
| "Push a pull day to my watch" | Creates structured workout → preview → confirm → uploads to Garmin |
| "Bump bench to 45kg" | Modifies existing Garmin workout |
| "How's my recovery?" | Shows HRV/sleep/readiness with computed verdict |
| "Show me my ski speed trend" | Sends a matplotlib chart as photo |
| "My achievements" | Shows unlocked achievements, streaks, current challenge |
| "I switched to Anytime Fitness" | Updates memory — remembers for future sessions |
| "What was my last leg workout?" | Searches memory and workout history |

### Proactive push notifications

The bot doesn't wait for you to ask. A cron job (`reflect`) runs daily and:

1. **Syncs** latest Garmin data
2. **Detects patterns** — writes to `observations.md` (fatigue after run 5, HRV drops when you ignore rest advice, speed is better after 7h+ sleep)
3. **Checks achievements** — unlocks and notifies ("Speed Demon 40: Hit 40 km/h!")
4. **Scores urgency** — new ski session? HRV declining 3 days? 5 days inactive?
5. **Sends analysis** — new ski/gym activity gets a full breakdown with chart, not a generic "nice workout"

### Morning briefing

Cron sends a daily briefing with recovery chart:

> GOOD to go. HRV 58, slept 7.5h — third night above 7h, nice streak. Day 2 of skiing though, remember you fade after run 5. Keep it tight.

### The coach remembers

AI-managed markdown files that evolve over time:

| File | What's in it |
|------|-------------|
| `soul.md` | Coach personality — direct, occasionally sarcastic, data-backed |
| `profile.md` | Your info, goals, injuries, sport history |
| `observations.md` | **Data-driven patterns** (auto-detected, not LLM-guessed) |
| `gym.md` | Equipment at your gym |

Observations are the moat. Examples the system auto-detects:
- "Ski fatigue pattern: speed drops after run 5 (seen in 6/8 sessions)"
- "Trained on 3/4 low-readiness days — HRV dropped avg 12% next day"
- "Ski speed averages 41.5 km/h after 7h+ sleep vs 38.2 after <7h"
- "Most active on Sat, Sun. Never trains on Wednesday."

The coach references these in every interaction for accountability.

### Visual feedback

| Feature | When |
|---------|------|
| **Trend charts** (ski speed, gym volume, recovery) | Proactive with analysis + on-demand in chat |
| **PR achievement cards** | Auto-sent when you break a record |
| **Weekly report** (4-panel chart + summary) | `garmin-coach weekly` on Sundays |

### Gamification

**14 achievements** — First Blood, Ski Rat (10 sessions), Speed Demon 30/40/50, Ton Lifter, Week Warrior, Comeback Kid, etc.

**Streaks** — consecutive training days, sleep 7h+ nights, consecutive ski days.

**Weekly challenge** — auto-generated from your current level: "Hit 42.1 km/h this week (current best: 40.6)".

### Measure your own impact

```bash
garmin-coach impact --days 30
```

Pure numbers, no LLM. Shows whether the bot actually changed your behavior:
- Run budget compliance (stopped before fatigue?)
- Recovery compliance (rested on LOW days?)
- Speed/weight progression
- Sleep trend

## Commands

| Command | What | How to run |
|---------|------|-----------|
| `garmin-onboard` | Setup wizard | Once |
| `garmin-coach bot` | Telegram bot | systemd service |
| `garmin-coach morning` | Morning briefing + chart | Cron daily |
| `garmin-coach reflect` | Sync → observe → achieve → notify | Cron daily |
| `garmin-coach weekly` | Weekly report with chart | Cron Sundays |
| `garmin-coach impact` | Coach effectiveness report | Manual |
| `garmin-coach sync` | One-shot data sync | Manual |
| `garmin-coach analyze` | Analyze latest activity | Manual |

### Cron setup

```bash
0 7 * * * /path/to/.venv/bin/garmin-coach morning
0 12 * * * /path/to/.venv/bin/garmin-coach reflect
0 20 * * * /path/to/.venv/bin/garmin-coach reflect
0 10 * * 0 /path/to/.venv/bin/garmin-coach weekly
```

## Architecture

```
src/
├── ai/
│   ├── coach.py           # LLM interface, memory management
│   ├── insights.py        # Computed analytics (ski, gym, recovery, pre-ski briefing)
│   ├── observations.py    # Data-driven pattern detection (6 detectors)
│   ├── gamification.py    # Achievements (14), streaks (3), challenges
│   ├── charts.py          # matplotlib chart generation (3 chart types)
│   ├── pr_card.py         # PR achievement card images
│   ├── weekly_report.py   # Weekly summary chart + text
│   ├── impact.py          # Coach effectiveness measurement
│   ├── notify.py          # Event scoring + frequency control (5 event types)
│   └── prompts/           # 9 LLM prompt templates
├── bot/
│   ├── agent.py           # PydanticAI agent with 11 tools
│   └── telegram.py        # Telegram message handling + photo sending
├── garmin/
│   ├── client.py          # Garmin Connect API wrapper
│   ├── sync.py            # Data sync orchestration
│   ├── fit_parser.py      # FIT file parsing (gym sets, ski runs)
│   └── workout.py         # Workout upload to Garmin
├── db/models.py           # SQLite schema (8 tables)
├── config.py              # YAML config
├── setup.py               # Interactive onboarding wizard
└── main.py                # CLI entry points (8 commands)
```

**Design principle:** Every number shown to the user is computed by Python, not estimated by the LLM. The LLM is a presenter, not an analyst.

## LLM Providers

Any OpenAI-compatible API works:

| Provider | model | base_url |
|----------|-------|----------|
| OpenAI | `gpt-4o` | (default) |
| Gemini | `gemini-2.0-flash` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| OpenRouter | `google/gemini-2.0-flash-001` | `https://openrouter.ai/api/v1` |
| Local (Ollama) | `llama3` | `http://localhost:11434/v1` |

## Roadmap

### Done
- PydanticAI agent (11 tools) + Telegram bot
- Computed insights (ski speed/fatigue, gym volume/plateau, recovery)
- Garmin data sync (HRV, sleep, body battery, activities, FIT parsing)
- Workout upload to Garmin watch
- Memory system (AI-managed markdown + observations)
- Event-driven notifications with frequency control
- Trend charts, PR cards, weekly reports
- Achievement system, streaks, challenges
- Coach personality with accountability
- Impact measurement
- Interactive setup wizard

### Future
- [ ] **Voice briefings** — TTS morning briefing as Telegram voice message
- [ ] **Progressive Overload** — auto PR detection, plateau → deload suggestions
- [ ] **Workout auto-evolution** — post-training feedback → AI updates Garmin workout
- [ ] **Training periodization** — weekly/monthly volume, overtraining detection

## Device Compatibility

Tested on Forerunner 955. Works with any Garmin watch that syncs to Garmin Connect — coaching is server-side via Telegram.

Optional: HR-Based Rest Connect IQ Data Field for strength training rest timing (see `hr-based-rest/`).

## License

MIT
