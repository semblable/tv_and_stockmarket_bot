# Import necessary modules
import discord
from discord.ext import commands
import config  # For loading the bot token
import os
import asyncio
from flask import Flask
from threading import Thread

# Check if the token was loaded correctly
if config.DISCORD_BOT_TOKEN is None:
    print("Error: DISCORD_BOT_TOKEN is not set. Please create a .env file with your bot token.")
    exit() # Exit if the token is not found

# Define intents
intents = discord.Intents.default()
intents.message_content = True # Enable message content intent for potential future use

# Create a Bot instance
bot = commands.Bot(command_prefix="/", intents=intents) # Using "/" as prefix for slash commands

# --- Cog Loading ---
INITIAL_EXTENSIONS = [
    "cogs.tv_shows",
    "cogs.stocks"
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
@bot.tree.command(name="ping", description="Responds with Pong!")
async def ping(interaction: discord.Interaction):
    """
    A simple slash command that responds with 'Pong!'.
    """
    await interaction.response.send_message("Pong!")

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