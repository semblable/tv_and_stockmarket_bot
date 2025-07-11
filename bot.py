# Import necessary modules
import discord
from discord.ext import commands
from discord import app_commands # Required for @app_commands.describe
import config  # For loading the bot token
import os
import logging # Import logging
from cogs.help import MyCustomHelpCommand # Import the custom help command
import asyncio
import traceback # Added for detailed error logging
from flask import Flask, request, jsonify
from threading import Thread
from functools import wraps
from data_manager import DataManager # For API endpoints
import random # For placeholder chart data

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
    "cogs.movies", # Added the new Movies Cog#
    "cogs.gemini", # New Gemini AI Cog
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

# --- Global Application Command Error Handler ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """
    Global error handler for slash commands.
    """
    # Log the full error traceback
    command_name = interaction.command.name if interaction.command else "unknown_command"
    logger.error(f"Unhandled error in slash command '/{command_name}': {error}", exc_info=False) # exc_info=False because print_exc will show it
    traceback.print_exc()

    error_message = "Sorry, an unexpected error occurred while processing your command. The developers have been notified."

    if interaction.is_response_done():
        try:
            # If the interaction has been responded to or deferred, try sending a followup message.
            await interaction.followup.send(error_message, ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to send followup error message for '/{command_name}': {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while trying to send followup for '/{command_name}': {e}", exc_info=True)
    else:
        try:
            # If the interaction has not been responded to yet, send a new response.
            await interaction.response.send_message(error_message, ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Failed to send initial error message for '/{command_name}': {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while trying to send initial response for '/{command_name}': {e}", exc_info=True)

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

# --- Initialize DataManager ---
# This should be done once, and the instance can be shared.
# Ensure Oracle credentials are loaded by config.py before this.
try:
    db_manager = DataManager()
    bot.db_manager = db_manager # Assign DataManager instance to the bot object
    logger.info("DataManager initialized successfully.")
except Exception as e:
    logger.critical(f"CRITICAL: Failed to initialize DataManager: {e}", exc_info=True)
    bot.db_manager = None # Ensure it's None if initialization fails
    # Depending on the bot's design, you might want to exit or prevent Flask/bot from starting.
    # For now, it will log critically and proceed, but API endpoints will likely fail.

# --- Internal API Endpoints ---
@flask_app.route('/api/internal/user/<discord_user_id>/tv_subscriptions', methods=['GET'])
@require_internal_api_key
def get_tv_subscriptions(discord_user_id):
    # Access db_manager via bot object
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
    flask_app.logger.info(f"[BOT API LOGGER] Attempting to get TV subscriptions for discord_user_id: {discord_user_id}")
    try:
        user_id = int(discord_user_id)
        flask_app.logger.info(f"[BOT API LOGGER] Converted discord_user_id to int user_id: {user_id}")
        subscriptions = bot.db_manager.get_user_tv_subscriptions(user_id) # Use instance via bot
        flask_app.logger.info(f"[BOT API LOGGER] Data manager returned TV subscriptions for user_id {user_id}: {subscriptions}")
        # get_user_tv_subscriptions now returns a list, potentially empty. No need to check for None.
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
    # Access db_manager via bot object
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
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

    flask_app.logger.info(f"[BOT API LOGGER] Calling db_manager.add_tv_show_subscription for user_id {user_id}, tmdb_id {tmdb_id}")
    success = bot.db_manager.add_tv_show_subscription(user_id, tmdb_id, title, poster_path) # Use instance via bot

    if success:
        flask_app.logger.info(f"[BOT API LOGGER] Successfully added/found TV show (tmdb_id: {tmdb_id}) for user_id {user_id}.")
        return jsonify({"message": "TV show added successfully"}), 201
    else:
        flask_app.logger.error(f"[BOT API LOGGER] Failed to add TV show (tmdb_id: {tmdb_id}) for user_id {user_id} due to db_manager failure.")
        return jsonify({"error": "Failed to add TV show"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/tv_show/<int:tmdb_id>', methods=['DELETE'])
@require_internal_api_key
def remove_user_tv_show(discord_user_id, tmdb_id):
    # Access db_manager via bot object
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
    flask_app.logger.info(f"[BOT API LOGGER] Attempting to remove TV show tmdb_id: {tmdb_id} for discord_user_id: {discord_user_id}")
    try:
        user_id = int(discord_user_id)
    except ValueError:
        flask_app.logger.error(f"[BOT API LOGGER] ValueError converting discord_user_id '{discord_user_id}' to int for remove TV show.")
        return jsonify({"error": "Invalid user ID format"}), 400

    try:
        flask_app.logger.info(f"[BOT API LOGGER] Calling db_manager.remove_tv_show_subscription for user_id {user_id}, tmdb_id {tmdb_id}")
        removed_successfully = bot.db_manager.remove_tv_show_subscription(user_id, tmdb_id) # Use instance via bot

        if removed_successfully: # remove_tv_show_subscription returns True on successful commit
            # To check if a row was actually deleted, the DataManager method would need to return rowcount or similar
            flask_app.logger.info(f"[BOT API LOGGER] Successfully executed removal of TV show (tmdb_id: {tmdb_id}) for user_id {user_id}.")
            return "", 204
        else:
            # This implies the DB operation failed (e.g. connection issue during commit)
            flask_app.logger.warning(f"[BOT API LOGGER] db_manager.remove_tv_show_subscription returned False for user_id {user_id}, tmdb_id {tmdb_id}.")
            return jsonify({"error": "Failed to remove TV show due to database operation issue"}), 500

    except Exception as e:
        flask_app.logger.error(f"[BOT API LOGGER] Exception during removal of TV show (tmdb_id: {tmdb_id}) for user_id {user_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to remove TV show"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/movie', methods=['POST'])
@require_internal_api_key
def add_user_movie(discord_user_id):
    # Access db_manager via bot object
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
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

    flask_app.logger.info(f"[BOT API LOGGER] Calling db_manager.add_movie_subscription for user_id {user_id}, tmdb_id {tmdb_id}")
    success = bot.db_manager.add_movie_subscription(user_id, tmdb_id, title, poster_path) # Use instance via bot

    if success:
        flask_app.logger.info(f"[BOT API LOGGER] Successfully added/found movie (tmdb_id: {tmdb_id}) for user_id {user_id}.")
        return jsonify({"message": "Movie added successfully"}), 201
    else:
        flask_app.logger.error(f"[BOT API LOGGER] Failed to add movie (tmdb_id: {tmdb_id}) for user_id {user_id} due to db_manager failure.")
        return jsonify({"error": "Failed to add movie"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/movie_subscriptions', methods=['GET'])
@require_internal_api_key
def get_movie_subscriptions(discord_user_id):
    # Access db_manager via bot object
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
    try:
        user_id = int(discord_user_id)
        subscriptions = bot.db_manager.get_user_movie_subscriptions(user_id) # Use instance via bot
        return jsonify(subscriptions), 200
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        print(f"Error in /movie_subscriptions: {e}")
        return jsonify({"error": "Internal server error"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/tracked_stocks', methods=['GET'])
@require_internal_api_key
def get_tracked_stocks(discord_user_id):
    # Access db_manager via bot object
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
    try:
        user_id = int(discord_user_id)
        stocks = bot.db_manager.get_user_tracked_stocks(user_id) # Use instance via bot
        return jsonify(stocks), 200
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        print(f"Error in /tracked_stocks: {e}")
        return jsonify({"error": "Internal server error"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/tracked_stocks_with_prices', methods=['GET'])
@require_internal_api_key
def get_tracked_stocks_with_prices(discord_user_id):
    """Get tracked stocks with current prices and chart data"""
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
    
    from api_clients import alpha_vantage_client, yahoo_finance_client
    
    try:
        user_id = int(discord_user_id)
        stocks = bot.db_manager.get_user_tracked_stocks(user_id)
        
        enhanced_stocks = []
        for stock in stocks:
            symbol = stock['symbol']
            enhanced_stock = stock.copy()
            
            price_data = alpha_vantage_client.get_stock_price(symbol)
            data_source = "Alpha Vantage"
            
            if not price_data or "error" in price_data:
                price_data = yahoo_finance_client.get_stock_price(symbol)
                data_source = "Yahoo Finance"
            
            current_price_for_chart = 100 # Default for chart if price fetch fails
            if price_data and "05. price" in price_data:
                try:
                    current_price_float = float(price_data['05. price'])
                    enhanced_stock['current_price'] = current_price_float
                    current_price_for_chart = current_price_float # Use actual price for chart base
                    enhanced_stock['change'] = price_data.get('09. change', '0')
                    enhanced_stock['change_percent'] = price_data.get('10. change percent', '0%')
                    enhanced_stock['currency'] = price_data.get('currency', 'USD')
                    enhanced_stock['data_source'] = data_source
                    
                    if enhanced_stock.get('quantity') and enhanced_stock.get('purchase_price'):
                        current_value = current_price_float * enhanced_stock['quantity']
                        cost_basis = enhanced_stock['purchase_price'] * enhanced_stock['quantity']
                        enhanced_stock['current_value'] = current_value
                        enhanced_stock['cost_basis'] = cost_basis
                        enhanced_stock['total_gain_loss'] = current_value - cost_basis
                        if cost_basis != 0: # Avoid division by zero
                           enhanced_stock['total_gain_loss_percent'] = ((current_value - cost_basis) / cost_basis) * 100
                        else:
                            enhanced_stock['total_gain_loss_percent'] = 0 

                except (ValueError, TypeError):
                    enhanced_stock['current_price'] = None
                    enhanced_stock['error'] = 'Invalid price format from API'
                    # current_price_for_chart remains default 100
            else:
                enhanced_stock['current_price'] = None
                enhanced_stock['error'] = price_data.get('message', 'Unable to fetch price') if isinstance(price_data, dict) else 'Unable to fetch price'
                # current_price_for_chart remains default 100

            # Generate placeholder chart data (e.g., 30 data points)
            # In a real scenario, you'd fetch actual historical data here.
            placeholder_prices = []
            base_val = current_price_for_chart
            for i in range(30):
                # Simulate some variation around the base_val or a slight trend
                variation = (random.random() - 0.5) * (base_val * 0.05) # up to 5% variation
                trend_factor = (i - 15) * (base_val * 0.001) # slight trend
                placeholder_prices.append(max(0, round(base_val + variation + trend_factor, 2))) # ensure price is not negative
            enhanced_stock['chart_data'] = placeholder_prices
            
            enhanced_stocks.append(enhanced_stock)
        
        return jsonify(enhanced_stocks), 200
    except ValueError:
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        flask_app.logger.error(f"Error in /tracked_stocks_with_prices: {e}", exc_info=True) # Added exc_info
        return jsonify({"error": "Internal server error"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/tracked_stock', methods=['POST'])
@require_internal_api_key
def add_tracked_stock(discord_user_id):
    # Access db_manager via bot object
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
    flask_app.logger.info(f"[BOT API LOGGER] Attempting to add tracked stock for discord_user_id: {discord_user_id}")
    try:
        user_id = int(discord_user_id)
    except ValueError:
        flask_app.logger.error(f"[BOT API LOGGER] ValueError converting discord_user_id '{discord_user_id}' to int for add tracked stock.")
        return jsonify({"error": "Invalid user ID format"}), 400

    if not request.is_json:
        flask_app.logger.warning(f"[BOT API LOGGER] Add tracked stock request for user_id {user_id} is not JSON.")
        return jsonify({"error": "Invalid payload: request must be JSON"}), 400

    data = request.get_json()
    symbol = data.get('symbol')
    quantity = data.get('quantity')
    purchase_price = data.get('purchase_price')

    if not isinstance(symbol, str) or not symbol.strip():
        flask_app.logger.warning(f"[BOT API LOGGER] Invalid payload for add tracked stock for user_id {user_id}. Missing or invalid symbol. Payload: {data}")
        return jsonify({"error": "Invalid payload: missing or invalid symbol"}), 400

    # Validate optional numeric fields
    if quantity is not None:
        try:
            quantity = float(quantity)
            if quantity <= 0:
                return jsonify({"error": "Invalid payload: quantity must be positive"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid payload: quantity must be a number"}), 400

    if purchase_price is not None:
        try:
            purchase_price = float(purchase_price)
            if purchase_price <= 0:
                return jsonify({"error": "Invalid payload: purchase_price must be positive"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid payload: purchase_price must be a number"}), 400

    flask_app.logger.info(f"[BOT API LOGGER] Calling db_manager.add_tracked_stock for user_id {user_id}, symbol {symbol}")
    success = bot.db_manager.add_tracked_stock(user_id, symbol.strip().upper(), quantity, purchase_price) # Use instance via bot

    if success:
        flask_app.logger.info(f"[BOT API LOGGER] Successfully added/updated tracked stock ({symbol}) for user_id {user_id}.")
        return jsonify({"message": "Stock added/updated successfully"}), 201
    else:
        flask_app.logger.error(f"[BOT API LOGGER] Failed to add tracked stock ({symbol}) for user_id {user_id} due to db_manager failure.")
        return jsonify({"error": "Failed to add tracked stock"}), 500

@flask_app.route('/api/internal/user/<discord_user_id>/stock_alerts', methods=['GET'])
@require_internal_api_key
def get_stock_alerts(discord_user_id):
    flask_app.logger.info(f"[BOT API LOGGER] /stock_alerts: Entered for user {discord_user_id}")
    if not bot.db_manager:
        flask_app.logger.error("[BOT API LOGGER] /stock_alerts: bot.db_manager is None.")
        return jsonify({"error": "Database manager not available"}), 503
    try:
        flask_app.logger.info(f"[BOT API LOGGER] /stock_alerts: Attempting int conversion for {discord_user_id}")
        user_id = int(discord_user_id)
        flask_app.logger.info(f"[BOT API LOGGER] /stock_alerts: Converted to user_id {user_id}. Calling get_user_all_stock_alerts.")
        alerts = bot.db_manager.get_user_all_stock_alerts(user_id)
        alerts_type = type(alerts)
        alerts_len = len(alerts) if isinstance(alerts, (list, tuple, dict, str)) else 'N/A'
        flask_app.logger.info(f"[BOT API LOGGER] /stock_alerts: get_user_all_stock_alerts returned: {alerts_type} len: {alerts_len}")
        flask_app.logger.info(f"[BOT API LOGGER] /stock_alerts: Attempting jsonify.")
        response = jsonify(alerts)
        flask_app.logger.info(f"[BOT API LOGGER] /stock_alerts: jsonify successful. Returning response.")
        return response, 200
    except ValueError:
        flask_app.logger.error(f"[BOT API LOGGER] /stock_alerts: ValueError converting discord_user_id '{discord_user_id}' to int.", exc_info=True)
        return jsonify({"error": "Invalid user ID format"}), 400
    except Exception as e:
        flask_app.logger.error(f"[BOT API LOGGER] /stock_alerts: General Exception for discord_user_id {discord_user_id}: {e}", exc_info=True)
        try:
            return jsonify({"error": "Internal server error"}), 500
        except Exception as e_jsonify:
            flask_app.logger.error(f"[BOT API LOGGER] /stock_alerts: CRITICAL - jsonify failed in except block: {e_jsonify}", exc_info=True)
            return "Internal Server Error - jsonify failed", 500 # Plain text

@flask_app.route('/api/internal/user/<discord_user_id>/settings', methods=['GET'])
@require_internal_api_key
def get_user_settings(discord_user_id):
    # Access db_manager via bot object
    if not bot.db_manager:
        return jsonify({"error": "Database manager not available"}), 503
    try:
        user_id = int(discord_user_id)
        settings = bot.db_manager.get_user_all_preferences(user_id) # Use instance via bot
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
    logger.info(f"Bot is in {len(bot.guilds)} guild(s)")
    
    # Log bot's current permissions to check for applications.commands scope
    try:
        app_info = await bot.application_info()
        logger.info(f"Bot application ID: {app_info.id}")
        logger.info(f"Bot owner: {app_info.owner}")
        
        # Check bot permissions in each guild
        for guild in bot.guilds:
            logger.info(f"Guild: {guild.name} (ID: {guild.id}) - Members: {guild.member_count}")
            bot_member = guild.get_member(bot.user.id)
            if bot_member:
                perms = bot_member.guild_permissions
                logger.info(f"  Bot permissions in {guild.name}: Administrator: {perms.administrator}, Manage Guild: {perms.manage_guild}")
            else:
                logger.warning(f"  Bot member not found in {guild.name}")
    except Exception as e:
        logger.error(f"Error checking bot application info: {e}")

    # Sync commands
    commands_synced = False
    try:
        # If we have guilds, sync to the first one for immediate updates
        if bot.guilds:
            first_guild = bot.guilds[0]
            logger.info(f"Copying global commands to guild: {first_guild.name} (ID: {first_guild.id}) for immediate availability...")
            bot.tree.copy_global_to(guild=discord.Object(id=first_guild.id))
            # No need to clear and then copy, copy_global_to handles adding them.
            # If you wanted to ensure ONLY global commands and no pre-existing guild-specific ones,
            # you might clear first: bot.tree.clear_commands(guild=discord.Object(id=first_guild.id))
            # but copy_global_to should be sufficient for making them appear.

            logger.info(f"Attempting to sync application commands to guild: {first_guild.name} (ID: {first_guild.id})...")
            try:
                # Now sync the commands that were copied (or already existed) for this guild
                synced_guild = await bot.tree.sync(guild=discord.Object(id=first_guild.id))
                logger.info(f"✅ Successfully synced {len(synced_guild)} command(s) to guild {first_guild.name}")
                commands_synced = True
                
                # Log the synced commands
                for cmd in synced_guild:
                    logger.info(f"  - Synced command: /{cmd.name}")
                    
            except discord.Forbidden as e:
                logger.error(f"❌ Forbidden error syncing to guild {first_guild.name}: {e}")
                logger.error("This usually means the bot lacks 'applications.commands' scope or manage guild permissions")
            except discord.HTTPException as e:
                logger.error(f"❌ HTTP error syncing to guild {first_guild.name}: {e}")
            except Exception as e:
                logger.error(f"❌ Unexpected error syncing to guild {first_guild.name}: {e}")
        else:
            logger.warning("Bot is not in any guilds - cannot do guild-specific sync")
        
        # Also sync globally (takes up to 1 hour to propagate)
        logger.info("Attempting to sync application commands globally...")
        try:
            synced = await bot.tree.sync()
            logger.info(f"✅ Successfully synced {len(synced)} command(s) globally")
            if not commands_synced:
                commands_synced = True
        except discord.Forbidden as e:
            logger.error(f"❌ Forbidden error syncing globally: {e}")
            logger.error("This usually means the bot application lacks proper scopes")
        except discord.HTTPException as e:
            logger.error(f"❌ HTTP error syncing globally: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error syncing globally: {e}")
            
    except Exception as e:
        logger.error("❌ Critical error during command sync:", exc_info=True)
    
    if not commands_synced:
        logger.error("🚨 CRITICAL: No commands were synced! Slash commands will not work!")
        logger.error("🔧 SOLUTION: Ensure bot has 'bot' AND 'applications.commands' scopes when added to server")
        logger.error("🔧 REINVITE: Use this URL pattern: https://discord.com/api/oauth2/authorize?client_id=YOUR_BOT_ID&permissions=8&scope=bot%20applications.commands")
    else:
        logger.info("🎉 Command sync completed successfully!")

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