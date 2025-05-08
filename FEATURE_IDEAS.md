# Discord Bot: Feature Ideas & Improvements Plan

This document outlines potential improvements to existing commands and ideas for new, useful commands for the Discord bot.

## I. Brainstorm Enhancements for Existing Commands

### A. TV Shows Cog (`cogs/tv_shows.py`)

1.  **`my_tv_shows` Command:**
    *   **Suggestion:** Enhance to display more details for each subscribed show.
    *   **Rationale:** Currently shows name and ID. Adding information like the air date of the *next upcoming episode* (if available from TMDB) and the *name/season/episode number of the last episode the user was notified about* would provide more value.
2.  **`tv_unsubscribe` Command:**
    *   **Suggestion:** Implement a numbered reaction-based selection mechanism if multiple subscribed shows match the provided (potentially partial) name, similar to `tv_subscribe`.
    *   **Rationale:** Currently, if multiple partial matches occur, it lists them and asks the user to be more specific. A selection interface would be more user-friendly and consistent with the subscription process.
3.  **Notification Content/Format (Background Task):**
    *   **Suggestion:** Include a direct link to the show on TMDB (or a similar service) in the notification DM.
    *   **Rationale:** Allows users to quickly get more information about the show or episode.
    *   **Suggestion:** Allow users to optionally include the episode overview in the DM.
    *   **Rationale:** Some users might prefer a concise notification, while others might want the overview directly. This could be a user-configurable setting.

### B. Stocks Cog (`cogs/stocks.py`)

1.  **`track_stock` Command & Alert System:**
    *   **Suggestion:** Implement a stock alert system. When a user tracks a stock, allow them to set (optional) alert conditions.
    *   **Rationale:** The command name `track_stock` implies future alerts. This is a core value proposition for a stock tracking feature.
    *   **Alert Types:**
        *   Price reaches a target (above/below).
        *   Price changes by X% in a day/week.
        *   Significant volume spikes.
    *   **Configuration:** Users could set these via sub-commands (e.g., `!stock_alert_add <symbol> --target-above 150 --target-below 120`) or a more interactive flow.
2.  **`stock_price` Command:**
    *   **Suggestion:** Enhance to show more details like day's high/low, trading volume, 52-week high/low, and market cap.
    *   **Rationale:** Provides a more comprehensive snapshot of the stock's current status. Alpha Vantage likely provides this data.
3.  **`my_tracked_stocks` Command:**
    *   **Suggestion:** Display the current price alongside each tracked stock symbol.
    *   **Rationale:** Gives users a quick overview of their tracked stocks' performance without needing to run `stock_price` for each.

### C. General Bot Enhancements

1.  **User-Specific Notification Preferences:**
    *   **Suggestion:** Implement a command (e.g., `!settings` or `!preferences`) to allow users to customize notification behavior.
    *   **Rationale:** Users have different preferences for how and when they receive information.
    *   **Examples:**
        *   Toggle TV episode overview in DMs.
        *   Set a "do not disturb" time for notifications.
        *   Preferred notification style (e.g., embed vs. plain text for some alerts).
2.  **Enhanced `help` Command:**
    *   **Suggestion:** Ensure the `help` command is robust, auto-generated from command definitions if possible, and clearly explains all commands, their parameters, and provides examples.
    *   **Rationale:** Essential for usability as more commands are added.

## II. Brainstorm New Commands/Features

### A. TV Shows

1.  **`!tv_info <show_name>` Command:**
    *   **Suggestion:** A command to get detailed information about a specific TV show.
    *   **Rationale:** Useful for discovering new shows or getting more details than provided in subscribe/notification messages.
    *   **Details to include:** Overview/plot, genres, first air date, status (returning, ended), number of seasons/episodes, cast (main actors), TMDB rating, poster.
2.  **`!tv_schedule` Command:**
    *   **Suggestion:** A command to display a personalized schedule of upcoming episodes for the user's subscribed shows within a defined timeframe (e.g., next 7 days).
    *   **Rationale:** Helps users keep track of when their favorite shows are airing.
3.  **`!tv_trending` or `!tv_popular` Command:**
    *   **Suggestion:** Display a list of currently trending or popular TV shows from TMDB.
    *   **Rationale:** Helps users discover new shows.

### B. Stocks

1.  **Stock Charts Command (e.g., `!stock_chart <symbol> [timespan]`)**
    *   **Suggestion:** Generate and display a simple image-based chart of a stock's price over a specified period (e.g., 1D, 5D, 1M, 6M, 1Y).
    *   **Rationale:** Visual data is often easier to understand than raw numbers.
2.  **Company News Command (e.g., `!stock_news <symbol>`)**
    *   **Suggestion:** Fetch and display recent news articles related to a stock symbol.
    *   **Rationale:** Provides context for stock price movements.
3.  **Basic Portfolio Summary (More Complex):**
    *   **Suggestion:** Allow users to (optionally) add quantity and purchase price when tracking stocks. Then, a `!my_portfolio` command could show total value, overall gain/loss, and individual stock performance.
    *   **Rationale:** A significant step towards more comprehensive financial tracking.
    *   **Complexity Note:** This requires careful consideration of data storage, security/privacy, and more complex calculations.

### C. Utility/Fun Commands

1.  **`!weather <location>` Command:**
    *   **Suggestion:** Get current weather and a short forecast for a specified location.
    *   **Rationale:** A common and generally useful utility command.
2.  **`!movie_info <movie_name>` / `!movie_subscribe <movie_name>` (Similar to TV):**
    *   **Suggestion:** Extend TV show functionality to movies (search, info, perhaps release date notifications).
    *   **Rationale:** Leverages the existing TMDB client and similar interaction patterns.
3.  **Simple Poll Command (e.g., `!poll "Question" "Option A" "Option B" ...`)**
    *   **Suggestion:** Allow users to quickly create simple polls with emoji reactions.
    *   **Rationale:** Fun and engaging for a community.
4.  **`!upcoming_releases` (Games/Movies):**
    *   **Suggestion:** Show upcoming game or movie releases.
    *   **Rationale:** General interest for many communities. Requires relevant APIs.

## III. Flask Server Integration

*   **Suggestion:** A web dashboard for managing TV show subscriptions and stock tracking/alerts.
*   **Rationale:** Could offer a more user-friendly interface for managing a large number of subscriptions or complex alert configurations than Discord commands. Users could log in with Discord.
*   **Complexity Note:** This is a significant undertaking, involving web development, user authentication, and API communication.
*   **Other ideas:**
    *   Displaying aggregated, anonymized statistics (e.g., most popular subscribed shows on the server).
    *   Providing a public API endpoint that the bot could use, or for other services.

## IV. Prioritization/Categorization

*   **High Impact / Relatively Lower Effort (Good starting points):**
    *   Enhance `my_tv_shows` (more details).
    *   Enhance `stock_price` (more details).
    *   Enhance `my_tracked_stocks` (show current prices).
    *   Improve `tv_unsubscribe` with selection.
    *   Basic `!tv_info <show_name>` command.
    *   Robust `help` command.
*   **Medium Impact / Medium Effort:**
    *   `!tv_schedule` command.
    *   Stock alert system (basic: price target).
    *   `!stock_news <symbol>`.
    *   `!weather <location>`.
    *   Notification content improvements (TMDB link, optional overview).
*   **High Impact / High Effort (Complex):**
    *   Advanced stock alert system (percentage changes, complex conditions).
    *   Stock charts (`!stock_chart`).
    *   Basic portfolio summary (`!my_portfolio`).
    *   Flask web dashboard integration.
    *   Movie features (`!movie_info`, `!movie_subscribe`).
    *   User-specific notification preferences system.
*   **Nice to Have / Variable Effort:**
    *   `!tv_trending` / `!tv_popular`.
    *   Simple poll command.
    *   `!upcoming_releases` (games/movies).