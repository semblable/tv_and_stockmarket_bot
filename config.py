# Import necessary modules
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get the Discord bot token from environment variables
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

if DISCORD_BOT_TOKEN is None:
    print("Warning: DISCORD_BOT_TOKEN not found in environment variables. Make sure you have a .env file with the token.")
if TMDB_API_KEY is None:
    print("Warning: TMDB_API_KEY not found in environment variables. Make sure you have it set in your .env file for TV show features.")
if ALPHA_VANTAGE_API_KEY is None:
    print("Warning: ALPHA_VANTAGE_API_KEY not found in environment variables. Make sure you have it set in your .env file for stock features.")