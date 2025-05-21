# Import necessary modules
import os
import logging # Import logging
from dotenv import load_dotenv

# Get a logger instance (it might be configured by bot.py if this is imported after basicConfig)
# If this module is imported first, these logs might not show if basicConfig isn't called yet.
# However, bot.py now configures logging very early.
logger = logging.getLogger(__name__)
logger.info("config.py: Script execution started.")

# Load environment variables from .env file
logger.info("config.py: Attempting to load .env file...")
dotenv_path = os.path.join(os.path.dirname(__file__), '.env') # Explicitly define path to .env
if os.path.exists(dotenv_path):
    logger.info(f"config.py: .env file found at {dotenv_path}")
    load_dotenv(dotenv_path=dotenv_path)
    logger.info("config.py: load_dotenv() called.")
else:
    logger.warning(f"config.py: .env file NOT found at {dotenv_path}. Environment variables should be set directly.")


# Get the Discord bot token from environment variables
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")

# Oracle Database Configuration
ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
logger.info("config.py: os.getenv() called for all API keys and Oracle DB config.")

if DISCORD_BOT_TOKEN is None:
    logger.warning("config.py: DISCORD_BOT_TOKEN not found in environment variables.")
if TMDB_API_KEY is None:
    logger.warning("config.py: TMDB_API_KEY not found in environment variables.")
if ALPHA_VANTAGE_API_KEY is None:
    logger.warning("config.py: ALPHA_VANTAGE_API_KEY not found in environment variables.")
if OPENWEATHERMAP_API_KEY is None:
    logger.warning("config.py: OPENWEATHERMAP_API_KEY not found in environment variables.")
if INTERNAL_API_KEY is None:
    logger.warning("config.py: INTERNAL_API_KEY not found in environment variables.")
if ORACLE_USER is None:
    logger.warning("config.py: ORACLE_USER not found in environment variables.")
if ORACLE_PASSWORD is None:
    logger.warning("config.py: ORACLE_PASSWORD not found in environment variables.")
if ORACLE_DSN is None:
    logger.warning("config.py: ORACLE_DSN not found in environment variables.")

logger.info("config.py: Finished loading configuration.")