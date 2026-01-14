# TV, Stocks & Assistant Bot

A comprehensive Discord bot that combines entertainment tracking (TV shows, movies), financial monitoring (stocks + portfolio), utility features (weather), productivity (reminders/todos/habits), plus an AI assistant powered by Google Gemini.

## Features

### TV & Movies
- **Subscriptions**: get DM notifications when new episodes air / movies release
- **Info lookups**: rich embeds with ratings, air dates, networks, and posters (TMDB)
- **Trending & discovery**: see popular shows/movies

### Stocks & Portfolio
- **Quotes**: track US and international tickers (e.g. `NOV.DE`, `.WA`)
- **Tracking**: track positions (qty + purchase price) and show performance
- **Alerts**: price targets above/below and other monitoring
- **Charts**: price history charts and portfolio charts

### Weather
- **Slash command**: `/weather` for current conditions + forecast
- **Scheduled DMs**: optional scheduled weather notifications (configured via `settings`)

### Reminders & Productivity
- **Reminders**: one-off + repeating reminders (timezone-aware)
- **To-dos**: add/list/complete items with stats + graphs
- **Habits**: recurring habits with reminders, streaks, and analytics

### Books, Reading & Games
- **Author subscriptions**: get notified when an author releases a new book
- **Reading progress**: track pages / kindle / audiobook progress
- **Game info**: Steam-first lookups with Wikipedia/PCGamingWiki fallback

### AI Assistant
- **Gemini integration**: chat naturally with the bot for summaries, questions, and assistance

---

## Setup & Configuration

### Prerequisites
- Python 3.10+ (for local dev) or Docker
- Discord Bot Token
- TMDB API Key
- AlphaVantage API Key
- OpenWeatherMap API Key
- Google Gemini API Key

### Environment Variables
Create a `.env` file inside `tv_and_stockmarket_bot/` (same folder as `bot.py`).

Tip: copy `env.example` to `.env` and fill it in.

```env
DISCORD_BOT_TOKEN=your_discord_token
TMDB_API_KEY=your_tmdb_key
ALPHA_VANTAGE_API_KEY=your_av_key
OPENWEATHERMAP_API_KEY=your_owm_key
GEMINI_API_KEY=your_gemini_key
PUBLIC_BASE_URL=https://your-public-https-domain
```

---

## 🚀 Local Development

### Option 1: Using Docker (Recommended)
From the `tv_and_stockmarket_bot/` directory:

1. **Build the image:**
   ```bash
   docker build -f bot.Dockerfile -t discord-bot:latest .
   ```

2. **Run with persistence (PowerShell):**
   ```powershell
   .\docker-run-persistent.ps1
   ```
   
   *Or manually:*
   ```bash
   docker run -d --name bot-container \
     -v $(pwd)/data:/app/data \
     --env-file .env \
     discord-bot:latest
   ```

### Option 2: Direct Python
From the `tv_and_stockmarket_bot/` directory:

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the bot:**
   ```bash
   python bot.py
   ```

---

## Commands & Help

- **Prefix commands**: use `!` (example: `!help`, `!my_portfolio`)
- **Slash commands**: type `/` in Discord to browse and run commands
- **Custom help**:
  - `!help` shows categories + commands
  - `!help <command>` shows detailed help
  - `!help <category>` shows commands in a category

If you add the bot to a new server and slash commands don’t appear immediately, run `!sync` (requires Manage Server) to sync commands for that guild.

## ☁️ Deployment (Docker)

1. **Transfer files** to your server.
2. **Build the image:**
   ```bash
   docker build -f bot.Dockerfile -t discord-bot:latest .
   ```
3. **Run the container:**
   ```bash
   docker run -d --name bot-container \
     --restart unless-stopped \
     -p 5000:5000 \
     -v $(pwd)/data:/app/data \
     --env-file .env \
     discord-bot:latest
   ```

Note: port `5000` is used by the built-in Flask uptime endpoint and can be omitted if you don’t need to expose it.

---

## Xiaomi Band / Notify for Xiaomi → Webhook → Discord (per-user)

This bot supports ingesting health/sleep events via an HTTP webhook and routing them **per Discord user** using a secret URL.

### 1) Set your public base URL

Set `PUBLIC_BASE_URL` in `.env` to your public HTTPS domain (recommended), for example:

```env
PUBLIC_BASE_URL=https://your-service.onrender.com
```

### 2) Generate your personal secret webhook URL

In Discord (DM or server):

- `/xiaomi webhook`

The bot will DM you a URL like:

- `https://your-service.onrender.com/webhook/xiaomi/<YOUR_SECRET_TOKEN>`

Keep it private. Anyone who has it can POST data as “you”.

### 3) Configure Notify for Xiaomi (Android)

There are two common ways; both end with POSTing to your secret URL.

- **Option A — Notify “Custom Webhook” (if available in your app version)**
  - Create an automation/trigger for **sleep processed** (or sleep sync completed).
  - Set the action to **POST** to your secret URL above.
  - If Notify lets you choose body fields, include at least `event`, and optionally `value1/value2/value3`.

- **Option B — Tasker integration (recommended for reliability/custom payloads)**
  - Enable Notify’s **Tasker integration**.
  - In Tasker, create a Profile that triggers on the Notify event you want (sleep processed).
  - Add an action **Net → HTTP Request**:
    - Method: `POST`
    - URL: your secret URL
    - Body: JSON (example):
      ```json
      {"event":"sleep_processed","value1":"%someStart","value2":"%someEnd","value3":"%someSummary"}
      ```

### 4) What happens

When the bot receives a POST at your secret URL, it will DM you a summary + a small JSON snippet of what it received (useful for iterating on Notify/Tasker payloads).

## 📂 Data Persistence
The bot uses an SQLite database located at `data/app.db`.
- **Docker:** Map a host volume to `/app/data`.
- **Local:** The `data/` folder will be created automatically in your project root.

---

## Inviting the Bot
When inviting the bot to a server, make sure you include **both** scopes:
- `bot`
- `applications.commands`

The bot also needs the permissions required by the features you use (and must be able to DM users for DM notifications).

## License
MIT
