# Project Plan: Discord Bot - TV & Stock Notifications (with Web Service for Render)

**Goal:** Extend the existing Discord bot to provide users with notifications about upcoming TV show episodes, allow them to track and request stock market prices, and configure it to run as a web service for hosting on platforms like Render.

**Key Features:**

1.  **TV Show Notifications:**
    *   Users can subscribe to TV shows.
    *   The bot periodically checks for new episodes of subscribed shows using the TMDB API.
    *   Users are notified (e.g., via DM or a designated channel) when a new episode is airing soon or has aired.
2.  **Stock Market Tracking:**
    *   Users can track specific stock symbols.
    *   Users can request the current price of a tracked or any valid stock symbol using the Alpha Vantage API.
3.  **Web service endpoint for uptime monitoring.**

---

### I. Project Structure & Setup

1.  **Update Configuration:**
    *   Modify `.env.example` to include placeholders for `TMDB_API_KEY` and `ALPHA_VANTAGE_API_KEY`.
        ```diff
        DISCORD_BOT_TOKEN="YOUR_DISCORD_BOT_TOKEN_HERE"
        +TMDB_API_KEY="YOUR_TMDB_API_KEY_HERE"
        +ALPHA_VANTAGE_API_KEY="YOUR_ALPHA_VANTAGE_API_KEY_HERE"
        ```
    *   Update `config.py` to load these new API keys from environment variables.

2.  **Install New Libraries:**
    *   Add `requests` to `requirements.txt` for making HTTP requests to the APIs.
    *   Add `Flask` (a lightweight web framework) to `requirements.txt` to create the web service endpoint.
        ```diff
        discord.py
        python-dotenv
        +requests
        +Flask
        ```
    *   The user will need to run `pip install -r requirements.txt` again.

3.  **Create New Python Modules/Files:**
    *   `api_clients/tmdb_client.py`: Handles all interactions with the TMDB API.
    *   `api_clients/alpha_vantage_client.py`: Handles all interactions with the Alpha Vantage API.
    *   `cogs/tv_shows.py`: A discord.py Cog for all TV show related commands and background tasks.
    *   `cogs/stocks.py`: A discord.py Cog for all stock market related commands.
    *   `data_manager.py`: A module to handle reading from and writing to simple JSON files for storing user subscriptions and tracked stocks.
    *   `Procfile`: For Render deployment.

4.  **Update Core Bot (`bot.py`):**
    *   Load the new cogs (`tv_shows` and `stocks`).
    *   Integrate Flask to run a simple web server alongside the bot.

---

### II. TV Show Feature Implementation (`cogs/tv_shows.py` & `api_clients/tmdb_client.py`)

1.  **TMDB API Client (`api_clients/tmdb_client.py`):**
    *   Function to search for TV shows by name.
    *   Function to get details of a specific TV show by its TMDB ID.
    *   Helper functions to parse API responses.

2.  **Data Storage (`data_manager.py` for TV shows):**
    *   `tv_subscriptions.json` structure:
        ```json
        {
          "user_id_1": [
            {"show_id": 123, "show_name": "Example Show 1", "last_notified_episode_id": null},
            {"show_id": 456, "show_name": "Example Show 2", "last_notified_episode_id": 789}
          ],
          "user_id_2": [
            {"show_id": 123, "show_name": "Example Show 1", "last_notified_episode_id": null}
          ]
        }
        ```
    *   Functions in `data_manager.py`: `add_tv_subscription`, `remove_tv_subscription`, `get_user_tv_subscriptions`, `get_all_tv_subscriptions`, `update_last_notified_episode`.

3.  **TV Show Cog (`cogs/tv_shows.py`):**
    *   **Slash Commands:** `/tv_subscribe <show_name>`, `/tv_unsubscribe <show_name_or_id>`, `/my_tv_shows`.
    *   **Background Task (`tasks.loop`):** Periodically checks for new episodes, notifies users, and updates `last_notified_episode_id`.

---

### III. Stock Market Feature Implementation (`cogs/stocks.py` & `api_clients/alpha_vantage_client.py`)

1.  **Alpha Vantage API Client (`api_clients/alpha_vantage_client.py`):**
    *   Function to get the current price for a given stock symbol.
    *   Helper functions to parse API responses.

2.  **Data Storage (`data_manager.py` for Stocks):**
    *   `tracked_stocks.json` structure (optional, for "favorites"):
        ```json
        {
          "user_id_1": ["AAPL", "MSFT"],
          "user_id_2": ["GOOGL"]
        }
        ```
    *   Functions in `data_manager.py`: `add_tracked_stock`, `remove_tracked_stock`, `get_user_tracked_stocks`.

3.  **Stock Cog (`cogs/stocks.py`):**
    *   **Slash Commands:** `/stock_price <symbol>`, `/track_stock <symbol>`, `/untrack_stock <symbol>`, `/my_tracked_stocks`.

---

### IV. Web Service Integration & Render Deployment (`bot.py`, `Procfile`)

1.  **Modify `bot.py` for Web Service:**
    *   Import `Flask` and `Thread`.
    *   Initialize a Flask app:
        ```python
        from flask import Flask
        from threading import Thread
        import os # Make sure os is imported

        # ... (rest of your bot code, cogs loading etc.) ...

        flask_app = Flask(__name__)

        @flask_app.route('/')
        def home():
            return "Bot is alive!", 200

        def run_flask():
            port = int(os.environ.get("PORT", 5000))
            flask_app.run(host='0.0.0.0', port=port)

        if __name__ == "__main__":
            flask_thread = Thread(target=run_flask)
            flask_thread.start()

            if config.DISCORD_BOT_TOKEN: # Ensure config is imported and token is accessed
                bot.run(config.DISCORD_BOT_TOKEN)
            else:
                print("Critical Error: Bot token not found.")
        ```

2.  **Create `Procfile`:**
    *   Content: `web: python bot.py`

3.  **Environment Variables on Render:**
    *   `DISCORD_BOT_TOKEN`, `TMDB_API_KEY`, `ALPHA_VANTAGE_API_KEY`, `PYTHON_VERSION`.

---

### V. High-Level Architecture Diagram

```mermaid
graph TD
    User[Discord User] -- Slash Commands --> BotProcess[Bot Process on Render]
    UptimeMonitor[Uptime Monitor] -- HTTP Ping --> BotProcess

    BotProcess terdiri dari --> BotCore[Discord Bot Logic - bot.py]
    BotProcess terdiri dari --> WebServer[Flask Web Server - bot.py]

    BotCore -- Loads --> TVCog[TV Shows Cog - cogs/tv_shows.py]
    BotCore -- Loads --> StockCog[Stocks Cog - cogs/stocks.py]

    TVCog -- Uses --> TMDBClient[TMDB API Client - api_clients/tmdb_client.py]
    TVCog -- Uses --> DataManager[Data Manager - data_manager.py]
    TMDBClient -- HTTP Requests --> TMDB_API[TMDB API Service]

    StockCog -- Uses --> AlphaVantageClient[Alpha Vantage API Client - api_clients/alpha_vantage_client.py]
    StockCog -- Uses --> DataManager
    AlphaVantageClient -- HTTP Requests --> AlphaVantage_API[Alpha Vantage API Service]

    DataManager -- Reads/Writes --> TVSubsJSON[tv_subscriptions.json]
    DataManager -- Reads/Writes --> TrackedStocksJSON[tracked_stocks.json]

    BotCore -- Reads --> Config[config.py]
    Config -- Reads Env Vars on Render --> RenderEnvVars[Render Environment Variables]

    subgraph Render Platform
        BotProcess
        RenderEnvVars
    end
```

---

### VI. Next Steps & Considerations

1.  **API Key Acquisition:** User needs keys for TMDB and Alpha Vantage.
2.  **Error Handling:** Implement robust error handling.
3.  **Notification Preferences:** Decide on DM vs. channel for TV show notifications.
4.  **Rate Limiting:** Be mindful of API rate limits.
5.  **User Experience:** Provide clear feedback messages.
6.  **Scalability of Data Storage:** JSON is a starting point; consider a database for larger scale.
7.  **Render Specifics:** Configure Start Command, check logs, be aware of free tier limitations.