# TV and Stock Market Discord Bot

This is a Discord bot designed to provide notifications for upcoming TV show episodes and allow users to track stock market prices.

## Features

*   **TV Show Notifications:**
    *   Subscribe to TV shows via a command (e.g., `/tv_subscribe <show_name>`).
    *   Receive notifications for new episodes (planned).
    *   Powered by the [TMDB API](https://www.themoviedb.org/documentation/api).
*   **Stock Market Tracking:**
    *   Request current stock prices (e.g., `/stock_price <SYMBOL>`).
    *   Track specific stock symbols (planned).
    *   Powered by the [Alpha Vantage API](https://www.alphavantage.co/documentation/).
*   **Discord Integration:**
    *   Uses slash commands for interaction.
    *   Organized into Cogs for modularity.
*   **Deployment:**
    *   Configured to run as a web service for platforms like Render, using Flask for an uptime monitoring endpoint.
    *   Includes a `Procfile` for easy deployment.

## Project Structure

*   `bot.py`: Main bot application file, handles Flask integration and Cog loading.
*   `config.py`: Loads environment variables (API keys, bot token).
*   `.env.example`: Example environment file. Create your own `.env` based on this.
*   `requirements.txt`: Python dependencies.
*   `api_clients/`: Modules for interacting with external APIs (TMDB, Alpha Vantage).
*   `cogs/`: Discord.py Cogs for different bot functionalities (TV shows, stocks).
*   `data_manager.py`: Handles data persistence (e.g., user subscriptions) using JSON files.
*   `data/`: Directory where JSON data files will be stored (created automatically).
*   `Procfile`: Defines the process type for deployment platforms like Render.
*   `.gitignore`: Specifies intentionally untracked files that Git should ignore.
*   `PROJECT_PLAN.md`: Detailed plan for the project's features and architecture.

## Setup

1.  **Clone the repository (if you haven't already):**
    ```bash
    git clone https://github.com/semblable/tv_and_stockmarket_bot.git
    cd tv_and_stockmarket_bot
    ```
2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Set up environment variables:**
    *   Copy `.env.example` to a new file named `.env`.
    *   Fill in your `DISCORD_BOT_TOKEN`, `TMDB_API_KEY`, and `ALPHA_VANTAGE_API_KEY` in the `.env` file.
        *   Get your Discord Bot Token from the [Discord Developer Portal](https://discord.com/developers/applications).
        *   Get your TMDB API Key from [TMDB](https://www.themoviedb.org/settings/api).
        *   Get your Alpha Vantage API Key from [Alpha Vantage](https://www.alphavantage.co/support/#api-key).
5.  **Run the bot:**
    ```bash
    python bot.py
    ```

## Usage

Once the bot is running and invited to your Discord server:

*   `/ping`: Responds with "Pong!"
*   (Other commands for TV shows and stocks will be listed here as they are fully implemented)

## Contributing

(Details on how to contribute can be added here later if desired.)

## License

(Specify a license if you wish, e.g., MIT License.)