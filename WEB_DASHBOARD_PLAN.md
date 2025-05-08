# Web Dashboard Integration Plan

**Last Updated:** May 9, 2025

## 1. Analysis of Requirements

The primary goal is to create a Flask web dashboard for users to manage their Discord bot settings and data. This includes viewing and modifying TV/movie subscriptions, tracked stocks, stock alerts, and user preferences. Secure authentication via Discord OAuth2 is essential. A key challenge is managing concurrent data access between the bot and the dashboard, which currently relies on direct manipulation of JSON files.

## 2. Proposed Architecture

The system will consist of the following main components:

```mermaid
graph TD
    User[User via Web Browser] -->|HTTPS| FlaskDashboard[Flask Web Dashboard App]
    FlaskDashboard -->|Discord OAuth2| DiscordAuth[Discord OAuth2 Service]
    FlaskDashboard -->|Internal API (HTTP/HTTPS)| BotInternalAPI[Bot's Internal API (Flask)]
    BotInternalAPI -->|Reads/Writes| DataManager([data_manager.py])
    DataManager -->|Accesses| JSONFiles[JSON Data Files (e.g., tv_subscriptions.json)]
    DiscordBot[Discord Bot (Python)] -->|Uses| DataManager
    DiscordBot -->|Interacts with| DiscordAPI[Discord API]
    UserDiscord[User via Discord Client] -->|Interacts with| DiscordBot
```

*   **User:** Interacts with the dashboard via a web browser.
*   **Flask Web Dashboard App:** The new, primary Flask application serving the user interface. It will handle user requests, authentication, and communicate with the bot's internal API for data operations.
*   **Discord OAuth2 Service:** External service used for authenticating users.
*   **Bot's Internal API (Flask):** This will be an expansion of the existing Flask app running within the `bot.py` process. It will expose secure endpoints for the Flask Web Dashboard App to CRUD (Create, Read, Update, Delete) data. This API will be the *sole* interface for the dashboard to interact with the bot's data.
*   **DataManager (`data_manager.py`):** The existing Python module responsible for all read/write operations to the JSON files. The Bot's Internal API will use these functions.
*   **JSON Data Files:** The current data storage mechanism (e.g., `tv_subscriptions.json`, `tracked_stocks.json`).
*   **Discord Bot:** The existing bot application. It will continue to use `data_manager.py` for its operations.
*   **User (Discord Client):** Interacts with the bot via Discord commands.

## 3. Recommended Data Access Strategy: Internal API

Given that the bot already runs a Flask instance (`flask_app` in `bot.py`), the most robust and least disruptive approach is to **enhance this existing Flask app to serve as an internal API layer.**

*   **How it works:**
    1.  The main Flask Web Dashboard (a separate process) will make HTTP requests to specific endpoints exposed by the Flask app running *within* the Discord bot process.
    2.  These internal API endpoints in the bot will use the existing functions in `data_manager.py` to read or write data.
    3.  Since all data modifications (from bot commands or dashboard actions) go through `data_manager.py` within the *same bot process*, this significantly mitigates direct file access conflicts and race conditions. The internal API essentially serializes data access.

*   **Pros:**
    *   **Centralized Data Logic:** All data operations remain encapsulated within the bot's environment and `data_manager.py`.
    *   **Reduced Race Conditions:** Avoids two separate processes directly manipulating JSON files. The bot's process becomes the single source of truth for writes.
    *   **Leverages Existing Infrastructure:** Builds upon the Flask app already present in `bot.py`.
    *   **Security:** Communication between the dashboard and the bot's internal API can be secured (e.g., using a shared secret/API key, localhost-only binding if deployed on the same machine/network).

*   **Cons:**
    *   **IPC Complexity:** Introduces inter-process communication, though HTTP is standard.
    *   **API Development:** Requires defining and implementing the internal API endpoints.
    *   **Potential Bottleneck:** If the bot is extremely busy, API responsiveness might be affected, but this is less of a concern than data corruption.

*   **Alternatives Considered & Rejected for now:**
    *   **Direct File Access:** Too risky due to potential data corruption without complex file locking.
    *   **Dedicated Database (e.g., SQLite, PostgreSQL):** The most robust long-term solution, but involves a significant refactoring of `data_manager.py` and all data access points in the bot. This should be considered a future enhancement *after* the dashboard is functional. The API layer can initially abstract the JSON files and later be adapted to a database backend with minimal changes to the dashboard itself.

## 4. Suggested Technology Stack

*   **Backend (Web Dashboard):** Flask (as per requirement).
*   **Backend (Bot's Internal API):** Flask (extending the existing instance in `bot.py`).
*   **Frontend:**
    *   **HTML Templating:** Jinja2 (native to Flask).
    *   **Styling:** A simple CSS framework (e.g., Bootstrap, Tailwind CSS, or custom CSS) for a clean UI.
    *   **Interactivity (Optional Enhancement):** HTMX or Alpine.js for dynamic updates without a full JavaScript framework, keeping the frontend lightweight. Start with basic HTML forms and server-side rendering.
*   **Authentication:** Discord OAuth2. Libraries like `Flask-Dance` or `requests-oauthlib` can simplify this.
*   **Database:** Continue using JSON files via `data_manager.py` for the initial version, accessed through the internal API. Plan for a potential migration to SQLite or PostgreSQL in the future for better scalability and data integrity.

## 5. Outline Core Features & User Flow

**A. Authentication Flow:**
    1.  User visits the dashboard URL.
    2.  If not authenticated, user is redirected to a login page.
    3.  User clicks "Login with Discord."
    4.  User is redirected to Discord's OAuth2 authorization screen.
    5.  User authorizes the application.
    6.  Discord redirects back to the dashboard with an authorization code.
    7.  Dashboard exchanges the code for an access token and fetches user information (Discord ID).
    8.  User's Discord ID is stored in a secure session to identify them for subsequent requests to the bot's internal API.

**B. Core Functionality (Post-Authentication):**

*   **Navigation:** A sidebar or top navigation bar linking to sections:
    *   TV Shows
    *   Movies
    *   Tracked Stocks
    *   Stock Alerts
    *   User Settings

*   **TV Show Subscriptions:**
    *   **View:** Display a list/table of the user's subscribed TV shows (show name, last notified episode).
    *   **Add:** Form to search for a TV show (potentially using TMDB API via the bot's internal API) and add it to subscriptions.
    *   **Remove:** Button next to each subscription to remove it.

*   **Movie Subscriptions:**
    *   **View:** Display a list/table of the user's subscribed movies (movie title, release date, notified status).
    *   **Add:** Form to search for a movie and add it.
    *   **Remove:** Button to remove a movie subscription.

*   **Tracked Stocks:**
    *   **View:** Display a list/table of tracked stocks (symbol, quantity, purchase price if set).
    *   **Add/Update:** Form to add a new stock symbol or update quantity/purchase price for an existing one.
    *   **Remove:** Button to untrack a stock.

*   **Stock Alerts:**
    *   **View:** Display active stock alerts (symbol, target price above/below, DPC target above/below).
    *   **Set/Update:** Form to set or modify price/DPC targets for a tracked stock.
    *   **Remove:** Option to clear specific alert targets or all alerts for a stock.

*   **User Settings:**
    *   **View:** Display current settings (e.g., DND status, notification preferences).
    *   **Modify:** Forms to change these settings.

**C. Data Interaction Flow (Example: Adding a TV Show):**
    1.  User navigates to the "TV Shows" section and clicks "Add Show."
    2.  User enters a show name in a form and submits.
    3.  Flask Dashboard App receives the request.
    4.  Dashboard App makes an authenticated request to the Bot's Internal API (e.g., `POST /api/v1/user/{discord_id}/tv_shows`) with the show details.
    5.  Bot's Internal API endpoint receives the request, validates it, and calls `data_manager.add_tv_subscription()`.
    6.  `data_manager.py` updates the `tv_subscriptions.json` file.
    7.  Bot's Internal API returns a success/failure response to the Dashboard App.
    8.  Dashboard App renders an updated view or a confirmation message to the user.

## 6. Identified Challenges & Risks

*   **Data Consistency:** While the internal API helps, complex operations or high frequency bot-side changes could still lead to the dashboard displaying slightly stale data if not carefully managed (e.g., through optimistic updates or periodic refreshes). The API should ensure atomic operations where possible.
*   **Internal API Security:** The API between the dashboard and the bot needs to be secured. If they run on the same host, binding to `localhost` and using a shared secret token in headers would be a good start. If on different hosts, HTTPS and more robust authentication for the internal API are crucial.
*   **Discord OAuth2 Complexity:** Implementing OAuth2 correctly can be tricky, especially handling token refresh and secure storage.
*   **Scalability of JSON Files:** As user numbers and data grow, JSON file I/O will become a performance bottleneck. The API design should facilitate a future migration to a database backend.
*   **Error Handling & Resilience:** Robust error handling is needed for both the dashboard-to-API communication and the API-to-data_manager calls.
*   **Deployment Complexity:**
    *   The Discord bot (with its embedded Flask API) needs to be deployed.
    *   The Flask Web Dashboard app needs to be deployed separately.
    *   They need to be able to communicate (network configuration).
    *   Consider using Docker containers for both to simplify deployment and management.
*   **Bot Resource Usage:** The internal API will add some load to the bot process. Monitor performance.
*   **Rate Limiting:** If dashboard actions trigger external API calls through the bot (e.g., searching TMDB), be mindful of external API rate limits. The internal API can help centralize and potentially cache/queue such calls.