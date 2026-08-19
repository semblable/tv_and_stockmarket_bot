import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import ValidationError, model_validator
import os

# Initialize logger. 
# Note: If this module is imported before logging is configured in the entry point,
# these logs might rely on basicConfig or be lost/unformatted until then.
# ideally bot.py sets up logging before importing config.
logger = logging.getLogger(__name__)

_ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")


def _resolve_tmdb_secret(tmdb_key: str = "", tmdb_key_file: str = "") -> str:
    """
    Resolves the TMDB API key from secret sources in order of precedence:
    1. Explicit file path pointed to by TMDB_API_KEY_FILE.
    2. Conventional secret file locations (secrets/tmdb_api_key.txt, tmdb_api_key.txt, .env.tmdb, /run/secrets/tmdb_api_key).
    3. Explicit TMDB_API_KEY from environment or .env file.
    """
    # 1. Custom file path specified in TMDB_API_KEY_FILE
    if tmdb_key_file and os.path.isfile(tmdb_key_file):
        try:
            with open(tmdb_key_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content.startswith("TMDB_API_KEY="):
                    content = content.split("=", 1)[1].strip().strip('"\'')
                if content:
                    return content
        except Exception as e:
            logger.warning(f"Failed to read TMDB secret from {tmdb_key_file}: {e}")

    # 2. Conventional secret file locations
    base_dir = os.path.dirname(__file__)
    candidate_paths = [
        os.path.join(base_dir, "secrets", "tmdb_api_key.txt"),
        os.path.join(base_dir, "secrets", "TMDB_API_KEY"),
        os.path.join(base_dir, "secrets", "tmdb_secret.txt"),
        os.path.join(base_dir, "tmdb_api_key.txt"),
        os.path.join(base_dir, ".env.tmdb"),
        "/run/secrets/tmdb_api_key",
        "/run/secrets/TMDB_API_KEY",
    ]
    for path in candidate_paths:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content.startswith("TMDB_API_KEY="):
                        content = content.split("=", 1)[1].strip().strip('"\'')
                    if content:
                        return content
            except Exception as e:
                logger.warning(f"Failed to read TMDB secret from {path}: {e}")

    # 3. Fallback to TMDB_API_KEY from environment / .env
    if tmdb_key:
        return tmdb_key

    return ""


class Settings(BaseSettings):
    DISCORD_BOT_TOKEN: str
    TMDB_API_KEY: str = ""
    TMDB_API_KEY_FILE: str = ""
    ALPHA_VANTAGE_API_KEY: str
    OPENWEATHERMAP_API_KEY: str
    SQLITE_DB_PATH: str = "data/app.db"
    WEBHOOK_BASE_URL: str = "http://localhost:5000"
    WEBHOOK_SHARED_SECRET: str = ""
    WEBHOOK_MAX_BYTES: int = 50 * 1024
    WEBHOOK_RATE_LIMIT_PER_MIN: int = 30
    ALLOW_EXTERNAL_CHARTS: bool = True

    # Timer / Firebase sync (owner-only feature)
    FIREBASE_DATABASE_URL: str = ""
    FIREBASE_DATABASE_SECRET: str = ""
    TIMER_OWNER_ID: int = 0
    DISCORD_SYNC_SECRET: str = ""
    TIMER_AUTH_PASSWORD: str = ""

    # Clockify integration (per-user API keys; only override the base URL for non-global regions)
    CLOCKIFY_API_BASE_URL: str = "https://api.clockify.me/api/v1"

    # Config for pydantic settings
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",  # Try utf-8 first. If BOM issues persist, we might need handling.
        extra="ignore"
    )

    @model_validator(mode="after")
    def resolve_secrets(self) -> "Settings":
        resolved_tmdb = _resolve_tmdb_secret(self.TMDB_API_KEY, self.TMDB_API_KEY_FILE)
        if not resolved_tmdb:
            raise ValueError("TMDB_API_KEY is required. Provide it in .env, TMDB_API_KEY_FILE, or secrets/tmdb_api_key.txt.")
        self.TMDB_API_KEY = resolved_tmdb
        return self

try:
    # Attempt to load settings
    settings = Settings()
    
    # Export variables for backward compatibility
    DISCORD_BOT_TOKEN = settings.DISCORD_BOT_TOKEN
    TMDB_API_KEY = settings.TMDB_API_KEY
    TMDB_API_KEY_FILE = settings.TMDB_API_KEY_FILE
    ALPHA_VANTAGE_API_KEY = settings.ALPHA_VANTAGE_API_KEY
    OPENWEATHERMAP_API_KEY = settings.OPENWEATHERMAP_API_KEY
    SQLITE_DB_PATH = settings.SQLITE_DB_PATH
    WEBHOOK_BASE_URL = settings.WEBHOOK_BASE_URL
    WEBHOOK_SHARED_SECRET = settings.WEBHOOK_SHARED_SECRET
    WEBHOOK_MAX_BYTES = settings.WEBHOOK_MAX_BYTES
    WEBHOOK_RATE_LIMIT_PER_MIN = settings.WEBHOOK_RATE_LIMIT_PER_MIN
    ALLOW_EXTERNAL_CHARTS = settings.ALLOW_EXTERNAL_CHARTS
    FIREBASE_DATABASE_URL = settings.FIREBASE_DATABASE_URL
    FIREBASE_DATABASE_SECRET = settings.FIREBASE_DATABASE_SECRET
    TIMER_OWNER_ID = settings.TIMER_OWNER_ID
    DISCORD_SYNC_SECRET = settings.DISCORD_SYNC_SECRET
    TIMER_AUTH_PASSWORD = settings.TIMER_AUTH_PASSWORD
    CLOCKIFY_API_BASE_URL = settings.CLOCKIFY_API_BASE_URL
    
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
