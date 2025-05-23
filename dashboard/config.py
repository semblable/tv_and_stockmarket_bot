import os
from dotenv import load_dotenv

# Load environment variables from .env file
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)
else:
    # Try loading from current directory (Docker container case)
    load_dotenv('/app/.env')

class Config:
    SECRET_KEY = os.environ.get('DASHBOARD_SECRET_KEY') or 'your_default_secret_key'
    DISCORD_CLIENT_ID = os.environ.get('DASHBOARD_CLIENT_ID')
    DISCORD_CLIENT_SECRET = os.environ.get('DASHBOARD_CLIENT_SECRET')
    DISCORD_REDIRECT_URI = os.environ.get('DASHBOARD_REDIRECT_URI')
    
    # Internal Bot API settings
    BOT_INTERNAL_API_URL = os.environ.get('BOT_INTERNAL_API_URL')
    INTERNAL_API_KEY = os.environ.get('INTERNAL_API_KEY')

    # Discord API endpoints
    DISCORD_API_BASE_URL = 'https://discord.com/api'
    DISCORD_AUTHORIZATION_URL = f'{DISCORD_API_BASE_URL}/oauth2/authorize'
    DISCORD_TOKEN_URL = f'{DISCORD_API_BASE_URL}/oauth2/token'
    DISCORD_USER_INFO_URL = f'{DISCORD_API_BASE_URL}/users/@me'

    # OAuth2 Scopes
    DISCORD_SCOPES = ['identify', 'email', 'guilds']

# API Keys
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")

if not Config.DISCORD_CLIENT_ID or not Config.DISCORD_CLIENT_SECRET or not Config.DISCORD_REDIRECT_URI:
    print("Warning: Discord OAuth2 credentials (DASHBOARD_CLIENT_ID, DASHBOARD_CLIENT_SECRET, DASHBOARD_REDIRECT_URI) are not fully configured in .env")

if not Config.BOT_INTERNAL_API_URL:
    print("Warning: BOT_INTERNAL_API_URL is not configured in .env. Dashboard will not be able to communicate with the bot's API.")
if not Config.INTERNAL_API_KEY:
    print("Warning: INTERNAL_API_KEY is not configured in .env. Dashboard will not be able to authenticate with the bot's API.")