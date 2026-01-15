"""data_manager.py

Kept as a small compatibility facade.

The DataManager implementation is split across `data_manager_impl/` to keep
feature areas isolated and make the codebase easier to navigate.

Public API remains `from data_manager import DataManager`.
"""

from config import SQLITE_DB_PATH

from data_manager_impl.core import DataManagerCore
from data_manager_impl.productivity import ProductivityMixin
from data_manager_impl.reminders import RemindersMixin
from data_manager_impl.media import MediaMixin
from data_manager_impl.stocks import StocksMixin
from data_manager_impl.prefs_weather import PrefsWeatherMixin
from data_manager_impl.books import BooksMixin
from data_manager_impl.reading import ReadingMixin
from data_manager_impl.games import GamesMixin
from data_manager_impl.mood import MoodMixin


class DataManager(
    DataManagerCore,
    ProductivityMixin,
    RemindersMixin,
    MediaMixin,
    StocksMixin,
    PrefsWeatherMixin,
    BooksMixin,
    ReadingMixin,
    GamesMixin,
    MoodMixin,
):
    def __init__(self) -> None:
        super().__init__(db_path=SQLITE_DB_PATH)
