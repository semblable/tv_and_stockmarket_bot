# Import logging setup first
import logger
logger.setup_logging()

# Import necessary modules
import discord
from discord.ext import commands
from discord import app_commands # Required for @app_commands.describe
import config  # For loading the bot token
import os
from cogs.help import MyCustomHelpCommand # Import the custom help command
import asyncio
import traceback # Added for detailed error logging
from flask import Flask
from threading import Thread
from data_manager import DataManager # For API endpoints
import random # For placeholder chart data

# Get logger
log = logger.get_logger(__name__)
log.info("Bot script started. Logging configured via logger.py.")

# Check if the token was loaded correctly
if config.DISCORD_BOT_TOKEN is None:
    log.error("CRITICAL: DISCORD_BOT_TOKEN is not set in config.py. The bot cannot start.")
    exit() # Exit if the token is not found
log.info("DISCORD_BOT_TOKEN found in config.")

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
    "cogs.books",  # New Books (author subscriptions)
    "cogs.reading_progress",  # Reading progress tracking (pages/kindle/audiobook)
    # "cogs.help" # Not loaded as a cog, but assigned directly
]

async def load_extensions():
    for extension in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(extension)
            log.info(f"Successfully loaded extension: {extension}")
        except Exception as e:
            log.error(f"Failed to load extension {extension}:", exc_info=True) # Log with traceback

# --- Global Application Command Error Handler ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """
    Global error handler for slash commands.
    """
    # Log the full error traceback
    command_name = interaction.command.name if interaction.command else "unknown_command"
    log.error(f"Unhandled error in slash command '/{command_name}': {error}", exc_info=False) # exc_info=False because print_exc will show it
    traceback.print_exc()

    error_message = "Sorry, an unexpected error occurred while processing your command. The developers have been notified."

    if interaction.is_response_done():
        try:
            # If the interaction has been responded to or deferred, try sending a followup message.
            await interaction.followup.send(error_message, ephemeral=True)
        except discord.HTTPException as e:
            log.error(f"Failed to send followup error message for '/{command_name}': {e}")
        except Exception as e:
            log.error(f"An unexpected error occurred while trying to send followup for '/{command_name}': {e}", exc_info=True)
    else:
        try:
            # If the interaction has not been responded to yet, send a new response.
            await interaction.response.send_message(error_message, ephemeral=True)
        except discord.HTTPException as e:
            log.error(f"Failed to send initial error message for '/{command_name}': {e}")
        except Exception as e:
            log.error(f"An unexpected error occurred while trying to send initial response for '/{command_name}': {e}", exc_info=True)

# --- Flask Web Server for Render Uptime ---
flask_app = Flask(__name__)
# flask_app.logger.critical("!!!!!!!!!! BOT.PY HAS STARTED - LOGGER TEST !!!!!!!!!!") # New test log

@flask_app.route('/')
def home():
    return "Bot is alive and kicking!", 200 # Endpoint for uptime monitor

# --- Initialize DataManager ---
# This should be done once, and the instance can be shared.
try:
    db_manager = DataManager()
    bot.db_manager = db_manager # Assign DataManager instance to the bot object (temporary, for cogs that still use it)
    log.info("DataManager initialized successfully.")
except Exception as e:
    log.critical(f"CRITICAL: Failed to initialize DataManager: {e}", exc_info=True)
    bot.db_manager = None # Ensure it's None if initialization fails

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
    log.info(f"Bot is ready and logged in as {bot.user}")
    log.info(f"Bot is in {len(bot.guilds)} guild(s)")
    
    # Log bot's current permissions to check for applications.commands scope
    try:
        app_info = await bot.application_info()
        log.info(f"Bot application ID: {app_info.id}")
        log.info(f"Bot owner: {app_info.owner}")
        
        # Check bot permissions in each guild
        for guild in bot.guilds:
            log.info(f"Guild: {guild.name} (ID: {guild.id}) - Members: {guild.member_count}")
            bot_member = guild.get_member(bot.user.id)
            if bot_member:
                perms = bot_member.guild_permissions
                log.info(f"  Bot permissions in {guild.name}: Administrator: {perms.administrator}, Manage Guild: {perms.manage_guild}")
            else:
                log.warning(f"  Bot member not found in {guild.name}")
    except Exception as e:
        log.error(f"Error checking bot application info: {e}")

    # Sync commands
    commands_synced = False
    try:
        # If we have guilds, sync to the first one for immediate updates
        if bot.guilds:
            first_guild = bot.guilds[0]
            log.info(f"Copying global commands to guild: {first_guild.name} (ID: {first_guild.id}) for immediate availability...")
            bot.tree.copy_global_to(guild=discord.Object(id=first_guild.id))
            
            log.info(f"Attempting to sync application commands to guild: {first_guild.name} (ID: {first_guild.id})...")
            try:
                # Now sync the commands that were copied (or already existed) for this guild
                synced_guild = await bot.tree.sync(guild=discord.Object(id=first_guild.id))
                log.info(f"✅ Successfully synced {len(synced_guild)} command(s) to guild {first_guild.name}")
                commands_synced = True
                
                # Log the synced commands
                for cmd in synced_guild:
                    log.info(f"  - Synced command: /{cmd.name}")
                    
            except discord.Forbidden as e:
                log.error(f"❌ Forbidden error syncing to guild {first_guild.name}: {e}")
                log.error("This usually means the bot lacks 'applications.commands' scope or manage guild permissions")
            except discord.HTTPException as e:
                log.error(f"❌ HTTP error syncing to guild {first_guild.name}: {e}")
            except Exception as e:
                log.error(f"❌ Unexpected error syncing to guild {first_guild.name}: {e}")
        else:
            log.warning("Bot is not in any guilds - cannot do guild-specific sync")
        
        # Also sync globally (takes up to 1 hour to propagate)
        log.info("Attempting to sync application commands globally...")
        try:
            synced = await bot.tree.sync()
            log.info(f"✅ Successfully synced {len(synced)} command(s) globally")
            if not commands_synced:
                commands_synced = True
        except discord.Forbidden as e:
            log.error(f"❌ Forbidden error syncing globally: {e}")
            log.error("This usually means the bot application lacks proper scopes")
        except discord.HTTPException as e:
            log.error(f"❌ HTTP error syncing globally: {e}")
        except Exception as e:
            log.error(f"❌ Unexpected error syncing globally: {e}")
            
    except Exception as e:
        log.error("❌ Critical error during command sync:", exc_info=True)
    
    if not commands_synced:
        log.error("🚨 CRITICAL: No commands were synced! Slash commands will not work!")
        log.error("🔧 SOLUTION: Ensure bot has 'bot' AND 'applications.commands' scopes when added to server")
        log.error("🔧 REINVITE: Use this URL pattern: https://discord.com/api/oauth2/authorize?client_id=YOUR_BOT_ID&permissions=8&scope=bot%20applications.commands")
    else:
        log.info("🎉 Command sync completed successfully!")

# --- Basic Slash Command (Example) ---
@bot.hybrid_command(name="ping", description="Checks bot latency and responds with Pong!")
@app_commands.describe(
    ephemeral_response = "Whether the bot's response should only be visible to you (default: False)."
)
async def ping(ctx: commands.Context, ephemeral_response: bool = False):
    """
    Responds with 'Pong!' and the bot's latency.
    """
    latency_ms = round(bot.latency * 1000)
    await ctx.send(f"Pong! Latency: {latency_ms}ms", ephemeral=ephemeral_response)

# --- Manual Command Sync (for fast iteration / new guilds) ---
@bot.command(name="sync")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def sync_prefix(ctx: commands.Context):
    """
    Force-sync application (slash) commands to the current guild.
    This works as a PREFIX command: !sync
    """
    try:
        guild_id = ctx.guild.id
        bot.tree.copy_global_to(guild=discord.Object(id=guild_id))
        synced = await bot.tree.sync(guild=discord.Object(id=guild_id))
        await ctx.send(f"✅ Synced {len(synced)} command(s) to this server.")
        log.info(f"Manual sync: synced {len(synced)} commands to guild {guild_id}")
    except discord.Forbidden as e:
        log.error(f"Manual sync forbidden in guild {ctx.guild.id}: {e}")
        await ctx.send("❌ I don't have permission to sync commands here. Make sure I was invited with `applications.commands` and I have permission to manage the server.", ephemeral=True)
    except discord.HTTPException as e:
        log.error(f"Manual sync HTTP error in guild {ctx.guild.id}: {e}")
        await ctx.send(f"❌ Sync failed due to a Discord API error: {e}", ephemeral=True)
    except Exception as e:
        log.error("Manual sync unexpected error:", exc_info=True)
        await ctx.send(f"❌ Sync failed: {e}", ephemeral=True)

# --- Main Execution ---
async def main():
    log.info("Async main() function started.")
    # Start Flask app in a new thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    log.info("Flask thread started.")

    # Load cogs
    log.info("Attempting to load extensions (cogs)...")
    await load_extensions()
    log.info("Finished attempting to load extensions.")
    
    # Start the bot
    log.info("Starting Discord bot...")
    if config.DISCORD_BOT_TOKEN:
        await bot.start(config.DISCORD_BOT_TOKEN)
    else:
        log.critical("Bot token not found at the point of starting the bot.")

if __name__ == "__main__":
    log.info("Starting bot execution from __main__.")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot shutting down due to KeyboardInterrupt.")
    except Exception as e:
        log.error("An unexpected error occurred in __main__:", exc_info=True)
    finally:
        log.info("Bot has shut down.")
