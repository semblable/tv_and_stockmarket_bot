# tests/conftest.py
import os
import sys
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, patch

# Set dummy environment variables to pass config validation
os.environ["DISCORD_BOT_TOKEN"] = "dummy_token"
os.environ["TMDB_API_KEY"] = "dummy_tmdb_key"
os.environ["ALPHA_VANTAGE_API_KEY"] = "dummy_av_key"
os.environ["OPENWEATHERMAP_API_KEY"] = "dummy_owm_key"
os.environ["GEMINI_API_KEY"] = "dummy_gemini_key"
os.environ["SQLITE_DB_PATH"] = "bot_data.db" # Optional

# Add the package directory to sys.path
package_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'tv_and_stockmarket_bot'))
sys.path.insert(0, package_path)

# Also add the root project path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(1, project_root)

from data_manager import DataManager

@pytest.fixture
def db_manager():
    """Creates a DataManager instance with an in-memory database for testing."""
    import tempfile
    import shutil
    
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, "test_bot.db")
    
    # Patch SQLITE_DB_PATH in data_manager module to use our temp path
    with patch('data_manager.SQLITE_DB_PATH', temp_db_path):
        manager = DataManager()
        # Initialize DB (creates tables) - assuming DataManager does this in __init__ or has a method
        # Looking at DataManager code, it calls _initialize_db() in __init__
        
        yield manager
        
        # Cleanup
        manager.close()
        shutil.rmtree(temp_dir)

@pytest.fixture
def mock_bot():
    """Creates a mock Discord Bot."""
    bot = MagicMock()
    bot.loop = MagicMock()
    async def side_effect(executor, func, *args):
        return func(*args)
    
    bot.loop.run_in_executor.side_effect = side_effect
    return bot
