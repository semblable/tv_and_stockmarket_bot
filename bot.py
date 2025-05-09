# Import necessary modules
import discord
from discord.ext import commands
from discord import app_commands # Required for @app_commands.describe
import config  # For loading the bot token
import os
from cogs.help import MyCustomHelpCommand # Import the custom help command
import asyncio
from flask import Flask, request, jsonify
from threading import Thread
from functools import wraps
import data_manager # For API endpoints

# Check if the token was loaded correctly
if config.DISCORD_BOT_TOKEN is None:
    print("Error: DISCORD_BOT_TOKEN is not set. Please create a .env file with your bot token.")
    exit() # Exit if the token is not found

# Define intents
intents = discord.Intents.default()
intents.message_content = True # Enable message content intent for potential future use

# Create a Bot instance
bot = commands.Bot(command_prefix="!", intents=intents, help_command=MyCustomHelpCommand()) # Using "!" as prefix for traditional commands and custom help

# --- Cog Loading ---
INITIAL_EXTENSIONS = [
    "cogs.tv_shows",
    "cogs.stocks",
    "cogs.utility",
    "cogs.settings",
    "cogs.movies" # Added the new Movies Cog
    # "cogs.help" # Not loaded as a cog, but assigned directly
]

async def load_extensions():
    for extension in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(extension)
            print(f"Successfully loaded extension: {extension}")
        except Exception as e:
            print(f"Failed to load extension {extension}: {e}")
            # Optionally, re-raise or handle more gracefully
            # raise e 

# --- Flask Web Server for Render Uptime ---
flask_app = Flask(__name__)

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
    print(f"Bot is ready and logged in as {bot.user}")
    try:
        # Sync the application commands (slash commands) to Discord
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")

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
    # Start Flask app in a new thread
    # Daemon threads exit when the main program exits
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask thread started.")

    # Load cogs
    await load_extensions()
    
    # Start the bot
    print("Starting Discord bot...")
    if config.DISCORD_BOT_TOKEN:
        await bot.start(config.DISCORD_BOT_TOKEN)
    else:
        # This case should ideally be caught by the check at the top,
        # but it's good practice to have a fallback.
        print("Critical Error: Bot token not found. Cannot start the bot.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot shutting down...")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        # Consider cleanup tasks if necessary, though bot.close() is handled by bot.start() on exit
        print("Bot has shut down.")