# Garmin AI Coach

AI training coach that syncs your Garmin data, analyzes it with Python, and coaches you via Telegram. Not a dashboard — a coach that remembers, learns patterns, and holds you accountable.

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

## How the Agent Works

When you send a message, this happens:

```
You: "How's my skiing going?"
                │
    ┌───────────▼───────────┐
    │   Context Injection    │  Reads soul.md, profile.md, observations.md
    │                        │  Fetches today's HRV/sleep/BB from DB
    │                        │  Detects anomalies (2-sigma deviations)
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   PydanticAI Agent     │  LLM sees full context + 10 tools
    │   (gpt-4o)             │  Decides: this needs get_insights("ski")
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   get_insights("ski")  │  Python computes: speed trend, plateau
    │                        │  detection, fatigue pattern, bottleneck
    │                        │  analysis. Returns pre-computed text.
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   LLM Presents         │  Combines computed insights + observations
    │                        │  into natural language with personality.
    │                        │  "Speed's up 8% this month. But you're
    │                        │   still fading after run 5 — that hasn't
    │                        │   changed. Work on pacing, not volume."
    └───────────┬───────────┘
                │
    ┌───────────▼───────────┐
    │   Telegram             │  Text response
    └───────────────────────┘
```

Key design: **Python computes every number. The LLM never does math.** It receives pre-computed insights and presents them in the coach's voice, referencing your history and behavioral patterns.

### What you can say

| You say | Agent calls | What happens |
|---------|-------------|-------------|
| "What should I train today?" | `generate_plan` | Generates plan based on HRV + sleep + recent sessions |
| "Push a pull day to my watch" | `push_workout` → `confirm_upload` | Generates structured workout → preview → confirm → uploads to Garmin |
| "Bump bench to 45kg" | `update_existing_workout` | Finds workout in tracker, modifies, re-uploads |
| "How's my recovery?" | `show_status` | Computed readiness verdict from HRV/sleep/RHR/Training Readiness |
| "How's my skiing going?" | `get_insights` | Speed trends, fatigue patterns, bottleneck analysis |
| "I switched to Anytime Fitness" | `update_memory` | Updates memory — remembers for future sessions |
| "What was my last leg workout?" | `search_memory` | Searches memory files + workout history |

All conversation flows are handled by the agent — no hardcoded pipelines. The agent decides which tools to call based on conversation context.

### Proactive push (no user action needed)

A cron job (`reflect`) runs daily and:

1. **Syncs** latest Garmin data
2. **Detects patterns** — writes to `observations.md` (fatigue after run 5, HRV drops when you ignore rest advice)
3. **Detects anomalies** — statistical 2-sigma scan across all metrics
4. **Scores urgency** — new ski session? HRV declining 3 days? 5 days inactive?
5. **Sends analysis** — new ski/gym activity gets a full analysis, not a generic notification

### Morning briefing

> GOOD to go. HRV 58, slept 7.5h — third night above 7h. Day 2 of skiing though, remember you fade after run 5. Keep it tight.

### The coach remembers and learns

AI-managed markdown files that evolve over time:

| File | What's in it |
|------|-------------|
| `soul.md` | Coach personality — direct, data-backed, occasionally sarcastic |
| `profile.md` | Your info, goals, injuries, sport history |
| `observations.md` | **Data-driven behavioral patterns** (auto-detected from data, not LLM-guessed) |

Observations are the moat. Examples the system auto-detects:
- "Ski fatigue pattern: speed drops after run 5 (seen in 6/8 sessions)"
- "Trained on 3/4 low-readiness days — HRV dropped avg 12% next day"
- "Ski speed averages 41.5 km/h after 7h+ sleep vs 38.2 after <7h"

The coach references these in every interaction for accountability.

### Know yourself

```bash
garmin-coach whoami
```

Computed user model — what the system knows about you from data (not what you told it):
- Training identity, physiological profile, behavioral patterns
- Progression trajectory, blind spots you might not notice
- If the output surprises you, the system is valuable

### Measure your own impact

```bash
garmin-coach impact --days 30
```

Pure numbers, no LLM:
- Run budget compliance (stopped before fatigue?)
- Recovery compliance (rested on LOW days?)
- Speed/weight progression
- Sleep trend

## Commands

| Command | What | How to run |
|---------|------|-----------|
| `garmin-onboard` | Setup wizard | Once |
| `garmin-coach bot` | Telegram bot | launchd / systemd |
| `garmin-coach morning` | Morning briefing | Cron daily |
| `garmin-coach reflect` | Sync → observe → notify | Cron daily |
| `garmin-coach impact` | Coach effectiveness report | Manual |
| `garmin-coach whoami` | Computed user model | Manual |
| `garmin-coach sync` | One-shot data sync | Manual |
| `garmin-coach analyze` | Analyze latest activity | Manual |

### Cron setup (launchd on macOS)

```bash
# Or add to crontab on Linux:
0 7 * * * /path/to/.venv/bin/garmin-coach morning
0 12 * * * /path/to/.venv/bin/garmin-coach reflect
0 20 * * * /path/to/.venv/bin/garmin-coach reflect
```

## Architecture

```
src/
├── ai/
│   ├── coach.py           # LLM interface, memory management
│   ├── insights.py        # Computed analytics (ski, gym, recovery, pre-ski briefing)
│   ├── observations.py    # Data-driven pattern detection (6 detectors)
│   ├── anomaly.py         # Statistical anomaly detection (2-sigma)
│   ├── user_model.py      # Computed user model (whoami)
│   ├── impact.py          # Coach effectiveness measurement
│   ├── notify.py          # Event scoring + frequency control
│   └── prompts/           # LLM prompt templates
├── bot/
│   ├── agent.py           # PydanticAI agent with 10 tools
│   └── telegram.py        # Telegram message handling
├── garmin/
│   ├── client.py          # Garmin Connect API wrapper
│   ├── sync.py            # Data sync orchestration
│   ├── fit_parser.py      # FIT file parsing (gym sets, ski runs)
│   └── workout.py         # Workout upload to Garmin
├── db/models.py           # SQLite schema
├── config.py              # YAML config
├── setup.py               # Interactive onboarding wizard
└── main.py                # CLI entry points
```

## LLM Providers

Any OpenAI-compatible API:

| Provider | model | base_url |
|----------|-------|----------|
| OpenAI | `gpt-4o` | (default) |
| Gemini | `gemini-2.0-flash` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| OpenRouter | `google/gemini-2.0-flash-001` | `https://openrouter.ai/api/v1` |
| Local (Ollama) | `llama3` | `http://localhost:11434/v1` |

## Roadmap

### Future (after 2 weeks of actual usage)
- [ ] **Progressive Overload** — auto PR detection, plateau → deload suggestions
- [ ] **Workout auto-evolution** — post-training feedback → AI updates Garmin workout
- [ ] **Training periodization** — weekly/monthly volume, overtraining detection

## Device Compatibility

Works with any Garmin watch that syncs to Garmin Connect — coaching is server-side via Telegram.

Optional: HR-Based Rest Connect IQ Data Field for strength training rest timing (see `hr-based-rest/`).

## License

MIT
