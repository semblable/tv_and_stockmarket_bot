import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import ValidationError
import os
import sys

# Initialize logger. 
# Note: If this module is imported before logging is configured in the entry point,
# these logs might rely on basicConfig or be lost/unformatted until then.
# ideally bot.py sets up logging before importing config.
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    DISCORD_BOT_TOKEN: str
    TMDB_API_KEY: str
    ALPHA_VANTAGE_API_KEY: str
    OPENWEATHERMAP_API_KEY: str
    GEMINI_API_KEY: str
    SQLITE_DB_PATH: str = "data/app.db"
    WEBHOOK_BASE_URL: str = "http://localhost:5000"

    # Config for pydantic settings
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",  # Try utf-8 first. If BOM issues persist, we might need handling.
        extra="ignore"
    )

try:
    # Attempt to load settings
    settings = Settings()
    
    # Export variables for backward compatibility
    DISCORD_BOT_TOKEN = settings.DISCORD_BOT_TOKEN
    TMDB_API_KEY = settings.TMDB_API_KEY
    ALPHA_VANTAGE_API_KEY = settings.ALPHA_VANTAGE_API_KEY
    OPENWEATHERMAP_API_KEY = settings.OPENWEATHERMAP_API_KEY
    GEMINI_API_KEY = settings.GEMINI_API_KEY
    SQLITE_DB_PATH = settings.SQLITE_DB_PATH
    WEBHOOK_BASE_URL = settings.WEBHOOK_BASE_URL
    
    logger.info("Configuration loaded successfully via Pydantic.")

except ValidationError as e:
    logger.critical("Configuration validation failed. Missing or invalid environment variables.")
    for error in e.errors():
        logger.critical(f"Field: {error['loc'][0]} - Error: {error['msg']}")
    # Re-raise to stop execution if config is invalid
    raise SystemExit("Critical: Invalid configuration. Check logs for details.")
except Exception as e:
    logger.critical(f"Unexpected error loading configuration: {e}")
    raise SystemExit(f"Critical: Unexpected error loading configuration: {e}")
