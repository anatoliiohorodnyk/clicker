# SimpleMMO Bot

Automated travel & resource farming bot for SimpleMMO.

## Features

- Auto travel with human-like delays
- Auto fight NPCs
- Auto gather materials
- Captcha solving (Google Gemini)
- Configurable break pauses
- Auto re-login

## Quick Start

```bash
git clone https://github.com/anatoliiohorodnyk/clicker.git
cd clicker
cp .env.example .env
# Edit .env with your credentials
docker compose up -d --build
```

## Environment Variables

Create `.env` file:

```env
# Required
GEMINI_API_KEY=your_gemini_api_key
SIMPLEMMO_EMAIL=your_email
SIMPLEMMO_PASSWORD=your_password

# Bot settings (optional - defaults shown)
STEP_DELAY_MIN=3
STEP_DELAY_MAX=8
STEPS_PER_SESSION=0          # 0 = infinite

# Break pauses
BREAK_INTERVAL_MIN=500       # steps before break
BREAK_INTERVAL_MAX=700
BREAK_DURATION_MIN=300       # break duration (seconds)
BREAK_DURATION_MAX=420

# Features
AUTO_FIGHT_NPC=true
AUTO_GATHER_MATERIALS=true
```

## Commands

```bash
# Start bot
docker compose up -d --build

# View logs
docker logs -f clicker-bot-1

# View logs with debug
docker compose run --rm bot python -m simplemmo_bot --verbose

# Stop bot
docker compose down
```

## TODO

- [ ] Web Panel (FastAPI + HTML/HTMX + SQLite)
  - Dashboard with statistics
  - Settings management (hot reload)
  - Account switching
  - Start/stop/pause controls
  - Live logs viewer
- [ ] Quests automation
- [ ] Battle Arena (NPC)
- [ ] Auto deposit gold to Bank
