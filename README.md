# Garmin AI Coach

AI-powered training coach for Garmin watches. Uses biometric data (HRV, sleep, heart rate) + LLM analysis for personalized gym and sport coaching via Telegram.

## Architecture

```
Garmin Watch ──sync──▶ Garmin Connect ──API──▶ Your Server
                                                   │
                                          ┌────────┴────────┐
                                          │   AI Coach       │
                                          │  • Garmin data   │
                                          │  • Memory system │
                                          │  • LLM (OpenAI)  │
                                          └────────┬────────┘
                                                   │
                                          ┌────────┴────────┐
                                          │  Telegram Bot    │
                                          │  • /plan         │
                                          │  • /morning      │
                                          │  • /memory       │
                                          │  • Free chat     │
                                          └─────────────────┘
```

## Features

### AI Coaching via Telegram
- `/plan` — AI-generated workout plan based on today's HRV, sleep, and training history
- `/plan chest` — Plan for specific muscle groups
- `/morning` — Daily briefing with body status and training recommendation
- `/memory` — View/update persistent memory (profile, gym equipment, preferences)
- `/sync` — Sync latest Garmin data
- `/status` — Current health metrics
- Free-form chat — ask anything about your training

### Memory System
AI-managed markdown files that persist across conversations. The bot remembers your profile, equipment, goals, and evolves over time.

```
data/memory/
├── soul.md       # Coach personality and principles (customizable)
├── profile.md    # Your info, goals, injuries, sport background
├── gym.md        # Available equipment at your gym
└── ...           # AI creates new files as needed
```

Update memory naturally via Telegram:
```
/memory I switched to Anytime Fitness, they have free barbells
/memory left knee pain after snowboarding, avoid deep squats
/memory my goal is lose 6kg by July
```

### Self-Evolution
A daily cron job (`reflect` command) that:
- Syncs Garmin data and reviews recent activity
- Detects training patterns (frequency, progress, plateaus)
- Records PRs and milestones to memory
- Sends proactive Telegram messages when needed ("You haven't trained in 5 days — HRV is high, good day to go")
- **Auto-analysis**: detects new ski/gym activity → sends full post-session analysis (not just a notification)

### Ski Intelligence
- **Pre-ski briefing**: morning briefing detects consecutive ski days → injects run budget, fatigue accumulation warning, and yesterday's fatigue data
- **Post-ski auto-analysis**: reflect detects new ski session → sends full per-run breakdown with speed trends, fatigue pattern, bottleneck analysis, and actionable conclusions
- **Season tracking**: speed progression, plateau detection, optimal session length, bottleneck identification (technique vs endurance vs recovery)

### HR-Based Rest (Connect IQ Data Field)
A Garmin Data Field for strength training rest optimization:
- Vibrates when heart rate recovers to target
- Three modes: Strength (120 bpm), Hypertrophy (140), Endurance (150)
- Configurable target HR values
- Works as an overlay on Garmin's native Strength Training activity

### Post-Workout Analysis
- Gym: cardiac drift detection, fatigue progression, recovery patterns
- Skiing/Snowboarding: per-run speed + HR recovery trends, optimal stop point, season progression

## Setup

### 1. Install

```bash
git clone https://github.com/bodhiforge/garmin-ai-coach.git
cd garmin-ai-coach
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`:

```yaml
garmin:
  email: "your-garmin-email"
  password: "your-garmin-password"

telegram:
  bot_token: "your-telegram-bot-token"  # Get from @BotFather
  chat_id: "your-chat-id"              # Get by messaging the bot

llm:
  api_key: "your-api-key"
  model: "gpt-4o-mini"
  # base_url: null  # set for non-OpenAI providers
```

Supported LLM providers (any OpenAI-compatible API):
| Provider | model | base_url |
|----------|-------|----------|
| OpenAI | `gpt-4o-mini` | (default) |
| Gemini | `gemini-2.0-flash` | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| Anthropic | `claude-haiku-4-5-20251001` | `https://api.anthropic.com/v1/` |
| OpenRouter | `google/gemini-2.0-flash-001` | `https://openrouter.ai/api/v1` |
| Local (Ollama) | `llama3` | `http://localhost:11434/v1` |

### 3. Personalize

Edit the memory files in `data/memory/`:

- **`soul.md`** — Coach personality and training philosophy. Customize the tone, principles, and coaching style.
- **`profile.md`** — Your personal info, training goals, injuries, sport background.
- **`gym.md`** — Equipment available at your gym.

Or update via Telegram: `/memory I have a home gym with dumbbells up to 50lb and a pull-up bar`

### 4. Run

```bash
# Sync Garmin data
python -m src.main sync

# Start Telegram bot
python -m src.main bot

# Morning briefing
python -m src.main morning --dry-run

# Self-reflection (run via cron)
python -m src.main reflect --dry-run
```

### 5. Cron Setup (Self-Evolution)

```bash
# Add to crontab — reflect twice daily
0 7 * * * /path/to/garmin-ai-coach/.venv/bin/python -m src.main reflect
0 20 * * * /path/to/garmin-ai-coach/.venv/bin/python -m src.main reflect
```

### 6. Connect IQ Data Field (Optional)

Requires [Connect IQ SDK](https://developer.garmin.com/connect-iq/sdk/):

```bash
brew install --cask connectiq
brew install lindell/connect-iq-sdk-manager-cli/connect-iq-sdk-manager-cli

# Generate developer key
mkdir -p ~/.ciq
openssl genrsa -out ~/.ciq/developer_key.pem 4096
openssl pkcs8 -topk8 -inform PEM -outform DER \
  -in ~/.ciq/developer_key.pem -out ~/.ciq/developer_key.der -nocrypt

# Build and deploy
cd hr-based-rest
monkeyc -d fr955 -f monkey.jungle -o bin/hr-rest.prg -y ~/.ciq/developer_key.der -w
# Copy hr-rest.prg to watch via USB/MTP → GARMIN/APPS/
```

## Roadmap

### Done
- [x] PydanticAI agent with 8 tools + Telegram bot
- [x] Computed insights layer (Python does the math, LLM presents)
- [x] Garmin data sync (HRV, sleep, body battery, activities, FIT parsing)
- [x] Workout upload to Garmin watch
- [x] Memory system (AI-managed markdown)
- [x] Event-driven notification system (Python scores urgency)
- [x] Garmin Training Readiness and Training Effect
- [x] Post-ski/gym auto-analysis in reflect
- [x] Pre-ski briefing with run budget

### Future (after data accumulation)
- [ ] **Progressive Overload** — auto PR detection, plateau detection → suggest deload or exercise variation
- [ ] **Workout auto-evolution** — post-training bot asks for feedback → AI updates Garmin workout
- [ ] **Training periodization** — weekly/monthly volume stats, overtraining detection, deload suggestions
- [ ] **Data visualization** — matplotlib charts sent to Telegram (speed trends, volume charts)

## Device Compatibility

Tested on Forerunner 955. Should work with any Garmin watch that syncs to Garmin Connect — the coaching happens server-side via Telegram. The Connect IQ Data Field requires a Connect IQ 4.2+ device.

## License

MIT
