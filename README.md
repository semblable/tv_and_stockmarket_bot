# 🤖 TV Show & Stock Discord Bot + Web Dashboard

A comprehensive Discord bot with a web dashboard for tracking TV shows, movies, stocks, and setting price alerts. Features OAuth2 authentication, real-time notifications, and interactive charts.

## ✨ Features

### 📺 TV Shows & Movies
- **TV Show Tracking**: Subscribe to TV shows and get notifications for new episodes
- **Interactive Selection**: Multi-choice selection when multiple shows match your search
- **Episode Notifications**: Automatic DM notifications with episode details and TMDB links
- **Schedule View**: See upcoming episodes for the next 7 days
- **Show Information**: Detailed show information with ratings, networks, and air dates
- **Trending Shows**: Browse currently trending TV shows
- **Movie Watchlist**: Track upcoming movie releases
- **TMDB Integration**: Search and browse content from The Movie Database
- **Web Dashboard**: Add/remove subscriptions through a user-friendly interface

### 📈 Stock Market
- **Stock Tracking**: Monitor stock prices with optional portfolio details (quantity & purchase price)
- **Price Alerts**: Set alerts for price thresholds and daily percentage changes
- **Interactive Charts**: Generate beautiful stock charts for various timeframes (1D, 1M, 6M, 1Y, YTD)
- **Portfolio View**: See your holdings, gains/losses, and current market values
- **News Integration**: Get recent news articles for tracked stocks
- **Dashboard Integration**: Add stocks to track via web interface

### 🔔 Notifications & Alerts
- **Stock Price Alerts**: Above/below price targets and daily percentage change alerts
- **TV Show Notifications**: Automatic notifications for new episodes with episode overviews
- **Movie Release Alerts**: Get notified when tracked movies are released
- **Rich Embeds**: Beautiful Discord embeds with posters, thumbnails, and detailed information

### 🌐 Web Dashboard
- **Discord OAuth2**: Secure login with Discord account
- **Unified Interface**: Manage all subscriptions from one place
- **Real-time Data**: Live integration with Discord bot's internal API
- **Mobile Responsive**: Works on desktop and mobile devices

## 🏗️ Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Discord Bot   │◄──►│   SQLite Database │◄──►│  Web Dashboard  │
│   (Port 5000)   │    │  (Persistent)     │    │   (Port 8050)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Alpha Vantage │    │   Local Storage   │    │   Discord OAuth │
│      TMDB       │    │   data/app.db     │    │      Login      │
│   Yahoo Finance │    │                  │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## 🚀 Quick Start

### Prerequisites
- Docker & Docker Compose
- Discord Developer Account
- API Keys (Alpha Vantage, TMDB, OpenWeatherMap)

### 1. Clone & Setup
```bash
git clone <repository-url>
cd "tvshow and stock bot"
```

### 2. Environment Configuration
Create a `.env` file from the `env.txt` template with your API keys and Discord OAuth settings:

```env
# Discord Bot Token
DISCORD_BOT_TOKEN=your_bot_token_here

# API Keys
TMDB_API_KEY=your_tmdb_key
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_key
OPENWEATHERMAP_API_KEY=your_weather_key

# Internal API Security
INTERNAL_API_KEY=generate_strong_random_key

# Dashboard OAuth2 (Discord Developer Portal)
DASHBOARD_CLIENT_ID=your_discord_oauth_client_id
DASHBOARD_CLIENT_SECRET=your_discord_oauth_secret
DASHBOARD_REDIRECT_URI=http://localhost:8050/callback
DASHBOARD_SECRET_KEY=generate_strong_flask_secret

# Database
SQLITE_DB_PATH=data/app.db
BOT_INTERNAL_API_URL=http://bot-container:5000
```

### 3. Run with Persistent Data
```bash
# Build containers
docker build -f bot.Dockerfile -t discord-bot .
docker build -f dashboard/Dockerfile -t project-dashboard .

# Start with persistent data (recommended)
.\docker-run-persistent.ps1
```

### 4. Access
- **Discord Bot**: Invite to your server using Discord Developer Portal
- **Web Dashboard**: http://localhost:8050
- **Internal API**: http://localhost:5000 (secured with API key)

## 🎯 Recent Updates & Fixes

### ✅ **TV Show System Overhaul** (Latest - May 2025)
- **Fixed Critical Bugs**: Resolved "thinking" state issues and function name mismatches
- **Hybrid Command Support**: Proper handling of both slash commands (`/`) and prefix commands (`!`)
- **Interactive Selection**: Multi-embed selection with reaction-based choosing
- **Rich Notifications**: Episode notifications with images, overviews, and TMDB links
- **Database Schema Updates**: Improved column names and JSON episode details storage
- **Error Handling**: Comprehensive error handling and user feedback
- **Background Tasks**: Automatic episode checking every 30 minutes
- **User Preferences**: Configurable episode overview notifications

### ✅ **Polish & International Stock Support** (May 2025)
- **Enhanced Stock Support**: Complete overhaul of international stock handling
- **Polish Stock Integration**: Automatic normalization of Polish stocks (LPP → LPP.WA, PKO → PKO.WA)
- **Yahoo Finance Fallback**: Robust fallback system when Alpha Vantage fails for international stocks
- **Currency Display**: Proper currency symbols (PLN: zł, EUR: €, GBP: £) instead of hardcoded $
- **Exchange Information**: Shows exchange details (WSE for Warsaw Stock Exchange)
- **Expanded Stock List**: Added 80+ Polish stocks (PZU, OPL, XTB, CCC, etc.)
- **Diagnostic Tools**: New `/stock_debug` command for troubleshooting API issues
- **Improved Error Handling**: Better fallback logic and user feedback

### ✅ **Stock Tracking in Dashboard** 
- **New Feature**: Added complete stock tracking functionality to web dashboard
- **Backend API**: New `POST /api/internal/user/{user_id}/tracked_stock` endpoint
- **Frontend Form**: Stock symbol, quantity, and purchase price input
- **Integration**: Full end-to-end workflow from web form to database

### ✅ **Chart Generation Fix** (Critical Bug Fix)
- **Issue**: Discord embed URLs were exceeding 2048 character limit
- **Solution**: Implemented QuickChart.io short URL service
- **Result**: Chart URLs reduced from 2000+ characters to ~74 characters
- **Impact**: All stock chart commands now work properly

### ✅ **Data Persistence** (Infrastructure)
- **Volume Mounts**: Added persistent storage for SQLite database
- **Convenience Script**: `docker-run-persistent.ps1` for easy container management
- **Location**: Database stored in local `data/` directory
- **Survives**: Container rebuilds, restarts, and system reboots

## 📊 Available Commands

### TV Show Commands
```
/tv_subscribe The Last of Us    # Subscribe to TV show with interactive selection
/tv_unsubscribe Breaking Bad    # Unsubscribe from TV show
/my_tv_shows                   # View all subscriptions with next episodes
/tv_info Stranger Things       # Get detailed show information
/tv_schedule                   # View upcoming episodes for next 7 days
/tv_trending                   # See trending TV shows (day/week)
```

### Stock Commands
```
/stock_price AAPL          # Get current stock price (US stock)
/stock_price LPP           # Get Polish stock price (auto-converts to LPP.WA)
/stock_price LPP.WA        # Get Polish stock price (explicit format)
/track_stock AAPL          # Start tracking a stock
/track_stock LPP 100 15500 # Track Polish stock with portfolio data
/my_tracked_stocks         # View all tracked stocks
/stock_chart AAPL 1M       # Generate price chart
/stock_chart LPP 6M        # Generate chart for Polish stock
/stock_alert AAPL above:200 # Set price alert
/stock_alert LPP above:16000 # Set alert for Polish stock (in PLN)
/my_portfolio              # View portfolio performance
/stock_news AAPL           # Get recent news
/stock_debug LPP           # Debug API connectivity (admin only)
```

**Supported International Stocks**:
- **Polish (WSE)**: LPP, PKO, PZU, OPL, PKN, JSW, PGE, CCC, XTB, KGH, etc. (80+ symbols)
- **European**: Stocks with .L (London), .PA (Paris), .AS (Amsterdam), .MI (Milan), .F (Frankfurt)
- **US**: All NASDAQ, NYSE, and other US exchanges

### Movie Commands
```
/add_movie Dune           # Add to watchlist
/movie_info Dune          # Get movie details
/my_movies               # View watchlist
```

### Gemini AI Commands
```
/gemini <prompt>                 # Chat with Google Gemini AI
/gemini new:true <prompt>        # Start a brand-new conversation
/gemini reset:true               # Reset/forget the current conversation
!gemini <prompt>                 # Prefix equivalent
!gemini new <prompt>             # Prefix: start new conversation
!gemini reset                    # Prefix: reset conversation
```

## 🔧 Development

### Project Structure
```
tvshow and stock bot/
├── bot.py                 # Main Discord bot
├── data_manager.py        # Database operations
├── config.py              # Configuration management (BOM-safe UTF-8)
├── docker-run-persistent.ps1 # Container management script
├── cogs/                  # Discord command modules
│   ├── stocks.py          # Stock commands
│   ├── tv_shows.py        # TV show commands (FIXED)
│   ├── movies.py          # Movie commands
│   ├── utility.py         # Utility commands
│   └── settings.py        # User preferences
├── dashboard/             # Web dashboard
│   ├── app.py             # Flask application
│   ├── internal_api_client.py # Bot API integration
│   ├── templates/         # HTML templates
│   └── static/            # CSS/JS assets
├── api_clients/           # External API integrations
│   ├── alpha_vantage_client.py # Stock data (US markets)
│   ├── yahoo_finance_client.py # Stock data (International + fallback)
│   ├── tmdb_client.py     # Movie/TV data (FIXED)
│   └── openweathermap_client.py # Weather data
├── utils/                 # Utilities
│   └── chart_utils.py     # Chart generation (FIXED)
└── data/                  # Persistent database storage
    └── app.db             # SQLite database
```

### Database Schema
- **Users**: Discord user information and preferences
- **TV Subscriptions**: User TV show tracking with episode notification history
- **Movie Subscriptions**: User movie watchlists
- **Tracked Stocks**: Stock symbols with optional portfolio data
- **Stock Alerts**: Price and percentage change alerts
- **User Preferences**: Configurable settings (episode overviews, etc.)

### API Endpoints (Internal)
```
GET  /api/internal/user/{user_id}/tv_subscriptions
POST /api/internal/user/{user_id}/tv_show
GET  /api/internal/user/{user_id}/movie_subscriptions
POST /api/internal/user/{user_id}/movie
GET  /api/internal/user/{user_id}/tracked_stocks
POST /api/internal/user/{user_id}/tracked_stock
GET  /api/internal/user/{user_id}/stock_alerts
GET  /api/internal/user/{user_id}/settings
```

## 🐛 Troubleshooting

### TV Show System Issues
- **Symptom**: Bot shows "thinking" instead of responding
- **Fix**: ✅ **RESOLVED** - Fixed function name mismatch (`search_tv_show` → `search_tv_shows`)
- **Solution**: Updated hybrid command handling for both slash (`/`) and prefix (`!`) commands
- **Test**: Try `/tv_subscribe The Last of Us` - should show interactive selection

### Polish/International Stock Issues
- **Symptom**: "Could not retrieve data from either Alpha Vantage or Yahoo Finance"
- **Solution**: Use `/stock_debug <symbol>` to test both APIs
- **Common Fix**: Restart bot container to load updated Yahoo Finance client
- **Verify**: Polish stocks should auto-convert (LPP → LPP.WA) and show PLN currency

### Chart Generation Issues
- **Fixed**: URLs were too long for Discord (>2048 chars)
- **Solution**: Now using QuickChart.io short URLs (~74 chars)
- **Test**: Run `python utils/chart_utils.py` to verify

### Configuration Encoding Issues
- **Issue**: BOM (Byte Order Mark) in .env files causing UnicodeDecodeError
- **Fix**: ✅ **RESOLVED** - Updated config.py to use `encoding='utf-8-sig'`
- **Prevention**: Use UTF-8 encoding without BOM when editing .env files

### Data Loss Between Restarts
- **Solution**: Use volume mounts with `docker-run-persistent.ps1`
- **Verify**: Check `data/app.db` exists and grows over time

### OAuth2 Login Issues
- **Check**: `DASHBOARD_REDIRECT_URI` matches Discord Developer Portal
- **Verify**: `DASHBOARD_CLIENT_ID` and `DASHBOARD_CLIENT_SECRET` are correct
- **Debug**: Check dashboard container logs

### Container Communication
- **Network**: Both containers must be on `app-network`
- **Internal URLs**: Use `http://bot-container:5000` for inter-container communication
- **External URLs**: Use `http://localhost:5000` and `http://localhost:8050`

## 🔮 Future Enhancements

### Planned Features
- **Crypto Tracking**: Add cryptocurrency price monitoring
- **Options Trading**: Support for options contracts
- **Social Features**: Share watchlists and portfolios
- **Mobile App**: Native mobile application
- **Advanced Charting**: Technical indicators and analysis tools
- **TV Show Pagination**: Handle users with many subscriptions
- **Episode Download Integration**: Link to streaming services

### API Integrations to Add
- **Polygon.io**: Higher-quality financial data
- **IEX Cloud**: Alternative stock data provider
- **CoinGecko**: Cryptocurrency data
- **Reddit API**: Sentiment analysis from r/stocks
- **Trakt.tv**: Additional TV show data and user statistics

## 📝 Contributing

1. **Fork** the repository
2. **Create** feature branch (`git checkout -b feature/amazing-feature`)
3. **Test** with `docker-run-persistent.ps1`
4. **Commit** changes (`git commit -m 'Add amazing feature'`)
5. **Push** to branch (`git push origin feature/amazing-feature`)
6. **Open** Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Discord.py**: Discord bot framework
- **Flask**: Web dashboard framework
- **Alpha Vantage**: Stock market data
- **Yahoo Finance**: International stock data fallback
- **TMDB**: Movie and TV show data
- **QuickChart.io**: Chart generation service
- **Docker**: Containerization platform

---

**Last Updated**: May 2025  
**Status**: ✅ Fully functional with TV show system fixes, Polish stock support, and dashboard integration  
**Version**: v2.1 - TV Show Overhaul Edition  
**Maintainer**: Ready for continued development in new sessions