# TV Show, Stock & AI Bot

A comprehensive Discord bot that combines entertainment tracking (TV shows, Movies) with financial monitoring (Stocks) and an AI assistant powered by Google Gemini.

## Features

### 🎬 TV & Movies
- **Show Subscriptions:** Receive DM notifications when new episodes air.
- **Movie Watchlist:** Track upcoming releases.
- **Rich Information:** Detailed embeds with ratings, air dates, networks, and posters (via TMDB).
- **Trending:** View popular shows and movies.

### 📈 Stocks
- **Real-time Quotes:** Track US and international stock prices (AlphaVantage / Yahoo Finance).
- **Smart Alerts:** Set price targets (above/below) and percentage change alerts.
- **Visual Charts:** View price history charts directly in Discord.

### 🤖 AI Assistant
- **Gemini Integration:** Chat naturally with the bot for summaries, questions, and assistance.
- **Context Aware:** Powered by Google's Gemini model.

---

## Setup & Configuration

### Prerequisites
- Python 3.10+ (for local dev) or Docker
- Discord Bot Token
- TMDB API Key
- AlphaVantage API Key (optional, for stocks)
- Google Gemini API Key (for AI features)

### Environment Variables
Create a `.env` file in the root directory:

```env
DISCORD_BOT_TOKEN=your_discord_token
TMDB_API_KEY=your_tmdb_key
ALPHA_VANTAGE_API_KEY=your_av_key
GEMINI_API_KEY=your_gemini_key
```

---

## 🚀 Local Development

### Option 1: Using Docker (Recommended)
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
1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the bot:**
   ```bash
   python bot.py
   ```

---

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

## 📂 Data Persistence
The bot uses an SQLite database located at `data/app.db`.
- **Docker:** Map a host volume to `/app/data`.
- **Local:** The `data/` folder will be created automatically in your project root.

---

## License.
MIT
