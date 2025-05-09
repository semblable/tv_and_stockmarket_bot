# Import necessary modules
import discord
from discord.ext import commands
from discord import app_commands # Required for @app_commands.describe
import config  # For loading the bot token
import os
import logging # Import logging
from cogs.help import MyCustomHelpCommand # Import the custom help command
import asyncio
from flask import Flask, request, jsonify
from threading import Thread
from functools import wraps
import data_manager # For API endpoints

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s', handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)
logger.info("Bot script started. Basic logging configured.")

# Check if the token was loaded correctly
if config.DISCORD_BOT_TOKEN is None:
    logger.error("CRITICAL: DISCORD_BOT_TOKEN is not set in config.py. The bot cannot start.")
    # It's possible config.py's print also didn't show, so log here too.
    # The original print in config.py should ideally handle the .env part.
    exit() # Exit if the token is not found
logger.info("DISCORD_BOT_TOKEN found in config.")

# Define intents
intents = discord.Intents.default()
intents.message_content = True # Enable message content intent for potential future use

# Create a Bot instance
bot = commands.Bot(command_prefix="!", intents=intents, help_command=MyCustomHelpCommand()) # Using "!" as prefix for traditional commands and custom help

## --- Cog Loading ---
INITIAL_EXTENSIONS = [
    "cogs.tv_shows",
    "cogs.stocks",
    "cogs.utility",
    "cogs.settings",
    "cogs.movies" # Added the new Movies Cog#
    # "cogs.help" # Not loaded as a cog, but assigned directly
]

async def load_extensions():
    for extension in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(extension)
            logger.info(f"Successfully loaded extension: {extension}")
        except Exception as e:
            logger.error(f"Failed to load extension {extension}:", exc_info=True) # Log with traceback
            # Optionally, re-raise or handle more gracefully
            # raise e 

# --- Flask Web Server for Render Uptime ---
flask_app = Flask(__name__)
flask_app.logger.critical("!!!!!!!!!! BOT.PY HAS STARTED - LOGGER TEST !!!!!!!!!!") # New test log

@flask_app.route('/')
def home():
    return "Bot is alive and kicking!", 200 # Endpoint for uptime monitor

# --- Internal API Key Security ---
def require_internal_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-Internal-API-Key')
        if not api_key or api_key != config.INTERNAL_API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        if not config.INTERNAL_API_KEY: # Should not happen if configured
            print("CRITICAL: INTERNAL_API_KEY is not configured in .env but API is being accessed.")
            return jsonify({"error": "Internal Server Configuration Error"}), 500
        return f(*args, **kwargs)
    return decorated_function

# --- Internal API Endpoints ---
@flask_app.route('/api/internal/user/<discord_user_id>/tv_subscriptions', methods=['GET'])
@require_internal_api_key
def get_tv_subscriptions(discord_user_id):
    flask_app.logger.info(f"[BOT API LOGGER] Attempting to get TV subscriptions for discord_user_id: {discord_user_id}")
    try:
        user_id = int(discord_user_id)
        flask_app.logger.info(f"[BOT API LOGGER] Converted discord_user_id to int user_id: {user_id}")
        subscriptions = data_manager.get_user_tv_subscriptions(user_id)
        flask_app.logger.info(f"[BOT API LOGGER] Data manager returned TV subscriptions for user_id {user_id}: {subscriptions}")
        if subscriptions is None:
            flask_app.logger.info(f"[BOT API LOGGER] No TV subscriptions found for user_id {user_id} (subscriptions is None), returning empty list.")
            return jsonify([]), 200 # Return empty list if no subscriptions found for a valid user
        return jsonify(subscriptions), 200
    except ValueError:
        flask_app.logger.error(f"[BOT API LOGGER] ValueError converting discord_user_id '{discord_user_id}' to int.")
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        flask_app.logger.error(f"[BOT API LOGGER] Error in /tv_subscriptions for discord_user_id {discord_user_id}: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
@flask_app.route('/api/internal/user/<discord_user_id>/tv_show', methods=['POST'])
@require_internal_api_key
def add_user_tv_show(discord_user_id):
    flask_app.logger.info(f"[BOT API LOGGER] Attempting to add TV show for discord_user_id: {discord_user_id}")
    try:
        user_id = int(discord_user_id)
    except ValueError:
        flask_app.logger.error(f"[BOT API LOGGER] ValueError converting discord_user_id '{discord_user_id}' to int for add TV show.")
        return jsonify({"error": "Invalid user ID format"}), 400

    if not request.is_json:
        flask_app.logger.warning(f"[BOT API LOGGER] Add TV show request for user_id {user_id} is not JSON.")
        return jsonify({"error": "Invalid payload: request must be JSON"}), 400

    data = request.get_json()
    tmdb_id = data.get('tmdb_id')
    title = data.get('title')
    poster_path = data.get('poster_path')

    if not all([isinstance(tmdb_id, int), isinstance(title, str), isinstance(poster_path, str)]):
        flask_app.logger.warning(f"[BOT API LOGGER] Invalid payload for add TV show for user_id {user_id}. Payload: {data}")
        return jsonify({"error": "Invalid payload: missing or incorrect type for tmdb_id, title, or poster_path"}), 400

    flask_app.logger.info(f"[BOT API LOGGER] Calling data_manager.add_tv_show_subscription for user_id {user_id}, tmdb_id {tmdb_id}")
    success = data_manager.add_tv_show_subscription(user_id, tmdb_id, title, poster_path)

    if success:
        flask_app.logger.info(f"[BOT API LOGGER] Successfully added/found TV show (tmdb_id: {tmdb_id}) for user_id {user_id}.")
        return jsonify({"message": "TV show added successfully"}), 201
    else:
        # This 'else' implies a database error from _save_json, as duplicates are handled as success.
        flask_app.logger.error(f"[BOT API LOGGER] Failed to add TV show (tmdb_id: {tmdb_id}) for user_id {user_id} due to data_manager failure.")
        return jsonify({"error": "Failed to add TV show"}), 500
@flask_app.route('/api/internal/user/<discord_user_id>/tv_show/<int:tmdb_id>', methods=['DELETE'])
@require_internal_api_key
def remove_user_tv_show(discord_user_id, tmdb_id):
    flask_app.logger.info(f"[BOT API LOGGER] Attempting to remove TV show tmdb_id: {tmdb_id} for discord_user_id: {discord_user_id}")
    try:
        user_id = int(discord_user_id)
        # tmdb_id is already an int due to <int:tmdb_id> in route
    except ValueError:
        flask_app.logger.error(f"[BOT API LOGGER] ValueError converting discord_user_id '{discord_user_id}' to int for remove TV show.")
        return jsonify({"error": "Invalid user ID format"}), 400

    try:
        flask_app.logger.info(f"[BOT API LOGGER] Calling data_manager.remove_tv_show_subscription for user_id {user_id}, tmdb_id {tmdb_id}")
        removed_successfully = data_manager.remove_tv_show_subscription(user_id, tmdb_id)

        if removed_successfully:
            flask_app.logger.info(f"[BOT API LOGGER] Successfully removed TV show (tmdb_id: {tmdb_id}) for user_id {user_id}.")
            return "", 204 # 204 No Content for successful DELETE
        else:
            # data_manager.remove_tv_show_subscription returned False.
            # This covers "show not found" or "save error after finding show".
            # As per prompt, this maps to a 404.
            flask_app.logger.warning(f"[BOT API LOGGER] TV show (tmdb_id: {tmdb_id}) not found for user_id {user_id} OR data_manager.remove_tv_show_subscription returned False.")
            return jsonify({"error": "TV show not found for this user"}), 404
            
    except Exception as e:
        # This covers other unexpected exceptions during the data_manager call or other logic.
        flask_app.logger.error(f"[BOT API LOGGER] Exception during removal of TV show (tmdb_id: {tmdb_id}) for user_id {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to remove TV show"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/movie', methods=['POST'])
@require_internal_api_key
def add_user_movie(discord_user_id):
    flask_app.logger.info(f"[BOT API LOGGER] Attempting to add movie for discord_user_id: {discord_user_id}")
    try:
        user_id = int(discord_user_id)
    except ValueError:
        flask_app.logger.error(f"[BOT API LOGGER] ValueError converting discord_user_id '{discord_user_id}' to int for add movie.")
        return jsonify({"error": "Invalid user ID format"}), 400

    if not request.is_json:
        flask_app.logger.warning(f"[BOT API LOGGER] Add movie request for user_id {user_id} is not JSON.")
        return jsonify({"error": "Invalid payload: request must be JSON"}), 400

    data = request.get_json()
    tmdb_id = data.get('tmdb_id')
    title = data.get('title')
    poster_path = data.get('poster_path')

    if not all([isinstance(tmdb_id, int), isinstance(title, str), isinstance(poster_path, str)]):
        flask_app.logger.warning(f"[BOT API LOGGER] Invalid payload for add movie for user_id {user_id}. Payload: {data}")
        return jsonify({"error": "Invalid payload: missing or incorrect type for tmdb_id, title, or poster_path"}), 400

    flask_app.logger.info(f"[BOT API LOGGER] Calling data_manager.add_movie_subscription for user_id {user_id}, tmdb_id {tmdb_id}")
    success = data_manager.add_movie_subscription(user_id, tmdb_id, title, poster_path)

    if success:
        flask_app.logger.info(f"[BOT API LOGGER] Successfully added/found movie (tmdb_id: {tmdb_id}) for user_id {user_id}.")
        return jsonify({"message": "Movie added successfully"}), 201
    else:
        # This 'else' implies a database error from _save_json, as duplicates are handled as success.
        flask_app.logger.error(f"[BOT API LOGGER] Failed to add movie (tmdb_id: {tmdb_id}) for user_id {user_id} due to data_manager failure.")
        return jsonify({"error": "Failed to add movie"}), 500
@flask_app.route('/api/internal/user/<discord_user_id>/movie_subscriptions', methods=['GET'])
@require_internal_api_key
def get_movie_subscriptions(discord_user_id):
    try:
        user_id = int(discord_user_id)
        subscriptions = data_manager.get_user_movie_subscriptions(user_id)
        if subscriptions is None: # Should return [] if not found
            return jsonify({"error": "User not found or no subscriptions"}), 404
        return jsonify(subscriptions), 200
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        print(f"Error in /movie_subscriptions: {e}")
        return jsonify({"error": "Internal server error"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/tracked_stocks', methods=['GET'])
@require_internal_api_key
def get_tracked_stocks(discord_user_id):
    try:
        user_id = int(discord_user_id)
        stocks = data_manager.get_user_tracked_stocks(user_id)
        if stocks is None: # Should return [] if not found
            return jsonify({"error": "User not found or no tracked stocks"}), 404
        return jsonify(stocks), 200
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        print(f"Error in /tracked_stocks: {e}")
        return jsonify({"error": "Internal server error"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/stock_alerts', methods=['GET'])
@require_internal_api_key
def get_stock_alerts(discord_user_id):
    try:
        user_id = int(discord_user_id)
        alerts = data_manager.get_user_all_stock_alerts(user_id) # Using the new function
        if alerts is None: # Should return {} if not found
             # data_manager.get_user_all_stock_alerts returns {} if user not found, which is fine
            pass
        return jsonify(alerts), 200
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        print(f"Error in /stock_alerts: {e}")
        return jsonify({"error": "Internal server error"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/settings', methods=['GET'])
@require_internal_api_key
def get_user_settings(discord_user_id):
    try:
        user_id = int(discord_user_id)
        settings = data_manager.get_user_all_preferences(user_id) # Using the new function
        if settings is None: # Should return {} if not found
            # data_manager.get_user_all_preferences returns {} if user not found, which is fine
            pass
        return jsonify(settings), 200
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        print(f"Error in /settings: {e}")
        return jsonify({"error": "Internal server error"}), 500

def run_flask():
    # Use '0.0.0.0' to be accessible externally.
    # Render typically sets the PORT environment variable.
    port = int(os.environ.get("PORT", 5000)) # Default to 5000 if PORT not set
    print(f"Starting Flask server on port {port}...")
    flask_app.run(host='0.0.0.0', port=port)

# --- Bot Events ---
@bot.event
async def on_ready():
    """
    Called when the bot is successfully logged in and ready.
    """
    logger.info(f"Bot is ready and logged in as {bot.user}")
    try:
        # Sync the application commands (slash commands) to Discord
        logger.info("Attempting to sync application commands...")
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error("Error syncing application commands:", exc_info=True)

# --- Basic Slash Command (Example) ---
@bot.hybrid_command(name="ping", description="Checks bot latency and responds with Pong!")
@app_commands.describe(
    ephemeral_response = "Whether the bot's response should only be visible to you (default: False)."
)
async def ping(ctx: commands.Context, ephemeral_response: bool = False):
    """
    Responds with 'Pong!' and the bot's latency.
    This command can be used to check if the bot is responsive.

    Usage examples:
    `!ping`
    `/ping`
    `/ping ephemeral_response:True`
    """
    latency_ms = round(bot.latency * 1000)
    await ctx.send(f"Pong! Latency: {latency_ms}ms", ephemeral=ephemeral_response)

# --- Main Execution ---
async def main():
    logger.info("Async main() function started.")
    # Start Flask app in a new thread
    # Daemon threads exit when the main program exits
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask thread started.")

    # Load cogs
    logger.info("Attempting to load extensions (cogs)...")
    await load_extensions()
    logger.info("Finished attempting to load extensions.")
    
    # Start the bot
    logger.info("Starting Discord bot...")
    if config.DISCORD_BOT_TOKEN: # This check is a bit redundant due to the one at the top, but safe
        await bot.start(config.DISCORD_BOT_TOKEN)
    else:
        # This case should ideally be caught by the check at the top,
        # but it's good practice to have a fallback.
        logger.critical("Bot token not found at the point of starting the bot. This should have been caught earlier.")

if __name__ == "__main__":
    logger.info("Starting bot execution from __main__.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shutting down due to KeyboardInterrupt.")
    except Exception as e:
        logger.error("An unexpected error occurred in __main__:", exc_info=True)
    finally:
        # Consider cleanup tasks if necessary, though bot.close() is handled by bot.start() on exit
        logger.info("Bot has shut down.")