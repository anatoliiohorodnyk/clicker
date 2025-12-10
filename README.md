# SimpleMMO Bot

Automated travel & resource farming bot for SimpleMMO with web control panel.

## Features

- **Web Control Panel** — Start/stop bot, view statistics, manage settings (mobile-friendly)
- **Multi-Account Support** — Add multiple accounts, switch between them
- **Auto Travel** — Human-like delays and break pauses
- **Auto Fight NPCs** — Automatically attack encountered NPCs
- **Auto Gather Materials** — Collect materials when found
- **Auto Equip Best Items** — Automatically equip strongest gear from inventory (per-account setting)
- **Quests Automation** — Auto-complete quests during breaks
- **Auto Respawn** — Healer (limited 3/day) or 5min auto-respawn
- **Auto Re-login** — Seamless session recovery
- **Captcha Solving** — Multiple AI providers:
  - Cloudflare Workers AI (free)
  - Google Gemini
  - OpenAI-compatible APIs

## Quick Start

```bash
git clone https://github.com/anatoliiohorodnyk/clicker.git
cd clicker
cp .env.example .env
# Edit .env with your credentials
docker compose up -d --build
```

Open `http://localhost:8080` in browser.

## Web Panel

Access the control panel at `http://localhost:8080`

- **Dashboard** — Bot status, session statistics, start/stop controls
- **Settings** — Configure delays, breaks, captcha provider, AI models
- **Accounts** — Add/edit/delete game accounts, see levels, enable auto-equip

## Environment Variables

Create `.env` file (see `.env.example` for full reference):

```env
# Logging
LOG_LEVEL=INFO

# Captcha AI Provider (choose one)
CAPTCHA_PROVIDER=cloudflare  # cloudflare, gemini, or openai

# ----- Cloudflare Workers AI (FREE, recommended) -----
OPENAI_API_BASE=https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1
OPENAI_API_KEY=your_cf_api_token
OPENAI_MODEL=@cf/llava-hf/llava-1.5-7b-hf

# ----- Gemini -----
# CAPTCHA_PROVIDER=gemini
# GEMINI_API_KEY=your_gemini_api_key
# GEMINI_MODEL=gemini-2.0-flash

# ----- OpenAI-compatible -----
# CAPTCHA_PROVIDER=openai
# OPENAI_API_BASE=https://api.openai.com/v1
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o

# SimpleMMO credentials (or add via Web Panel)
SIMPLEMMO_EMAIL=your_email
SIMPLEMMO_PASSWORD=your_password

# Bot settings
STEP_DELAY_MIN=5
STEP_DELAY_MAX=8
STEPS_PER_SESSION=0          # 0 = infinite

# Break pauses (quests run during breaks)
BREAK_INTERVAL_MIN=500       # steps before break
BREAK_INTERVAL_MAX=700
BREAK_DURATION_MIN=300       # break duration (seconds)
BREAK_DURATION_MAX=420

# Features
AUTO_FIGHT_NPC=true
AUTO_GATHER_MATERIALS=true
USE_HEALER=false             # use healer (3/day) or wait 5min auto-respawn
ONLY_QUESTS=false            # true = only quests, skip travel
```

## Docker Commands

```bash
# Start
docker compose up -d --build

# View logs
docker logs -f clicker-web-1

# Stop
docker compose down

# Rebuild after code changes
docker compose up -d --build
```

## Database

Bot data is stored in SQLite at `/data/bot.db` (mounted from host `/data` directory).

To backup:
```bash
cp /data/bot.db /data/bot.db.backup
```

## Cloudflare Workers AI Setup

1. Create Cloudflare account at [dash.cloudflare.com](https://dash.cloudflare.com)
2. Go to **AI** → **Workers AI**
3. Copy your **Account ID** from the URL or sidebar
4. Create **API Token** with `Workers AI` permission
5. Configure in `.env` or Web Panel Settings

**Verified vision models:**
- `@cf/llava-hf/llava-1.5-7b-hf` — tested, recommended
- `@cf/unum/uform-gen2-qwen-500m` — lightweight
- `@cf/meta/llama-3.2-11b-vision-instruct` — newest

## TODO

- [ ] Battle Arena (NPC)
- [ ] Auto deposit gold to Bank
- [ ] Telegram notifications
