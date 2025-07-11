# TV Show & Stock Bot + Mini Dashboard

A friendly Discord bot (Python) with a lightweight Flask dashboard.  
It reminds you about new TV-show episodes & movie releases **and** keeps an eye on stock prices – all in one place.

---

## Main features

### TV & Movies
- Subscribe to any show and get a DM when a new episode airs.
- Trending list, detailed info (ratings, network, air date).
- Movie watch-list with release notifications.

### Stocks
- Track real-time prices (US + international via AlphaVantage / YahooFinance).
- Price alerts (above / below target, % change).  
- Simple charts (QuickChart.io) included in embeds.

### Alerts & Embeds
- Rich Discord embeds with posters, thumbnails, links.  
- Background scheduler that checks episodes & prices every 30 min.

### Web dashboard
- Discord OAuth2 login.  
- One page to add / remove shows, movies and stocks.

---

## Tiny architecture
```
┌──────────────────┐    REST    ┌────────────────┐
│  Discord Bot     │◄──────────►│  Flask API      │
│  (bot.py)        │            │  (dashboard)    │
└──────────────────┘            └────────────────┘
        ▲   ▲                        ▲   ▲
        │   │                        │   │
        ▼   ▼                        ▼   ▼
  TMDB ▪ AlphaVantage ▪ YahooFinance ▪ SQLite (data/app.db)
```

---

## Quick start (Docker)
```bash
# clone & enter repo
git clone <repo_url>
cd "tvshow and stock bot"

# create .env and add at least
DISCORD_BOT_TOKEN=...
TMDB_API_KEY=...
ALPHA_VANTAGE_API_KEY=...

# build + run (persistent volume)
docker compose up -d      # or ./docker-run-persistent.ps1 on Windows
```
Bot will come online in Discord; dashboard runs on http://localhost:8050.

---

## Tech snapshot
Python 3 · discord.py · Flask · SQLite · Docker · GitHub Actions CI

## Project map (trimmed)
```
├─ bot.py            # Discord bot entry-point
├─ dashboard/        # Flask web app (OAuth + HTML)
├─ cogs/             # Command modules
├─ data_manager.py   # DB layer / queries
└─ api_clients/      # TMDB / Stock API wrappers
```

---

MIT license 
