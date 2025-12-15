import sqlite3
import os
import json
import logging
import threading
import datetime
from typing import List, Dict, Any, Optional, Union

logger = logging.getLogger(__name__)


class DataManagerCore:
    def __init__(self, db_path: str) -> None:
        if not db_path:
            logger.error("SQLite database path (db_path) not set.")
            raise ValueError("SQLite database path not set.")

        self.db_path = db_path

        try:
            logger.info(f"Attempting to connect to database at: {db_path}")

            db_dir = os.path.dirname(db_path)
            logger.info(f"Database directory derived as: {db_dir}")
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
                logger.info(f"Ensured directory exists: {db_dir}")

            self.conn = sqlite3.connect(db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._lock = threading.RLock()

            logger.info(f"Successfully connected to SQLite database at {db_path}.")
            self._initialize_db()
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to SQLite Database: {e}")
            raise ConnectionError(f"Failed to connect to SQLite Database: {e}")
        except OSError as e:
            logger.error(f"Failed to create directory for SQLite database: {e}")
            raise ConnectionError(f"Failed to create directory for SQLite database: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """Returns the active connection."""
        return self.conn

    def _close_connection(self, connection: sqlite3.Connection) -> None:
        """SQLite connections don't need explicit release like connection pools."""
        pass # No-op for SQLite single connection

    def _execute_query(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        fetch_one: bool = False,
        fetch_all: bool = False,
        commit: bool = False,
    ) -> Any:
        """
        Executes a given SQL query.

        Return values:
        - commit=True  -> bool (True on success)
        - fetch_one=True -> dict|None
        - fetch_all=True -> list[dict]
        - otherwise -> bool (True on success)
        """
        conn = self._get_connection()
        cursor = None
        # Connection is shared across threads; ensure serialized access.
        with self._lock:
            try:
                cursor = conn.cursor()
                if params:
                    # SQLite uses ? for placeholders, or named placeholders like :param_name
                    # We'll stick to named placeholders for consistency with previous Oracle code
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)

                if commit:
                    conn.commit()
                    return True

                if fetch_one:
                    row = cursor.fetchone()
                    return dict(row) if row else None # sqlite3.Row allows dict conversion
                elif fetch_all:
                    return [dict(row) for row in cursor.fetchall()] # sqlite3.Row allows dict conversion
                # We intentionally don't return cursors (they are closed below).
                return True
            except sqlite3.Error as e:
                logger.error(f"Database query error: {e}\nQuery: {query}\nParams: {params}")
                try:
                    conn.rollback()
                except sqlite3.Error as re:
                    logger.error(f"Rollback failed: {re}")
                if commit:
                    return False
                if fetch_one:
                    return None
                if fetch_all:
                    return []
                return False
            finally:
                if cursor:
                    cursor.close()

    def _initialize_db(self) -> None:
        """Creates tables if they don't exist."""
        # SQLite CREATE TABLE IF NOT EXISTS is the standard way
        
        # Helper to create table if not exists
        def create_table_if_not_exists(table_name: str, create_sql: str) -> None:
            # SQLite doesn't need the complex Oracle PL/SQL block
            # The CREATE TABLE IF NOT EXISTS syntax is sufficient
            ok = self._execute_query(create_sql, commit=True)
            if not ok:
                # _execute_query logs the underlying sqlite3.Error; treat schema failures as fatal.
                raise RuntimeError(f"Failed to create/check table {table_name}")
            logger.info(f"Table {table_name} checked/created.")

        # --- Start of tv_subscriptions schema check ---
        # Check if tv_subscriptions table exists and if it has the show_tmdb_id column
        table_info_query = "PRAGMA table_info(tv_subscriptions);"
        
        table_exists = False
        # Need a direct cursor for checking existence before PRAGMA, and to manage its lifecycle.
        conn = self._get_connection()
        check_cursor = None # Initialize to None
        try:
            check_cursor = conn.cursor()
            check_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tv_subscriptions';")
            if check_cursor.fetchone():
                table_exists = True
        except sqlite3.Error as e:
            logger.error(f"Error checking if 'tv_subscriptions' table exists: {e}")
            # If we can't even check, better to let the CREATE IF NOT EXISTS handle it or fail there.
        finally:
            if check_cursor:
                check_cursor.close()

        if table_exists:
            logger.info("Table 'tv_subscriptions' exists. Checking schema for 'show_tmdb_id' column.")
            # _execute_query with fetch_all=True returns a list of dicts
            columns_info = self._execute_query(table_info_query, fetch_all=True)
            
            if columns_info is None or not isinstance(columns_info, list):
                logger.error(f"Failed to retrieve schema for 'tv_subscriptions' (received: {columns_info}). Skipping schema modification check.")
            else:
                column_names = [col_info['name'] for col_info in columns_info if isinstance(col_info, dict) and 'name' in col_info]
                required_column = "show_tmdb_id"

                if required_column not in column_names:
                    logger.warning(
                        f"Column '{required_column}' not found in 'tv_subscriptions' table (columns found: {column_names}). "
                        "Dropping table to apply new schema. Existing TV subscriptions will be lost."
                    )
                    drop_query = "DROP TABLE tv_subscriptions;"
                    if self._execute_query(drop_query, commit=True):
                        logger.info("Old 'tv_subscriptions' table dropped successfully due to schema mismatch.")
                    else:
                        logger.error("Failed to drop 'tv_subscriptions' table. Manual intervention might be required.")
                else:
                    logger.info(f"'tv_subscriptions' schema contains '{required_column}'. No schema modification needed for this check.")

                # Check for show_tvmaze_id column (Added for TVMaze migration)
                if "show_tvmaze_id" not in column_names:
                    logger.info("Column 'show_tvmaze_id' not found in 'tv_subscriptions'. Adding it.")
                    alter_query = "ALTER TABLE tv_subscriptions ADD COLUMN show_tvmaze_id INTEGER;"
                    if self._execute_query(alter_query, commit=True):
                        logger.info("Column 'show_tvmaze_id' added successfully.")
                    else:
                        logger.error("Failed to add column 'show_tvmaze_id'.")
        else:
            logger.info("Table 'tv_subscriptions' does not exist. It will be created by 'CREATE TABLE IF NOT EXISTS'.")
        # --- End of tv_subscriptions schema check ---

        # --- Start of tracked_stocks schema check ---
        # Check if tracked_stocks table exists and if it has the currency column
        ts_table_exists = False
        conn = self._get_connection()
        check_cursor = None
        try:
            check_cursor = conn.cursor()
            check_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracked_stocks';")
            if check_cursor.fetchone():
                ts_table_exists = True
        except sqlite3.Error as e:
            logger.error(f"Error checking if 'tracked_stocks' table exists: {e}")
        finally:
            if check_cursor:
                check_cursor.close()

        if ts_table_exists:
            columns_info = self._execute_query("PRAGMA table_info(tracked_stocks);", fetch_all=True)
            if columns_info and isinstance(columns_info, list):
                column_names = [col_info['name'] for col_info in columns_info if isinstance(col_info, dict) and 'name' in col_info]
                if "currency" not in column_names:
                    logger.info("Column 'currency' not found in 'tracked_stocks'. Adding it.")
                    if self._execute_query("ALTER TABLE tracked_stocks ADD COLUMN currency TEXT;", commit=True):
                        logger.info("Column 'currency' added successfully to tracked_stocks.")
                    else:
                        logger.error("Failed to add column 'currency' to tracked_stocks.")
        # --- End of tracked_stocks schema check ---

        # TV Show Subscriptions
        # Storing last_notified_episode_details as TEXT for JSON
        create_tv_subscriptions_sql = """
        CREATE TABLE IF NOT EXISTS tv_subscriptions (
            user_id TEXT NOT NULL,
            show_tmdb_id INTEGER NOT NULL,
            show_name TEXT,
            poster_path TEXT,
            last_notified_episode_details TEXT,
            show_tvmaze_id INTEGER,
            PRIMARY KEY (user_id, show_tmdb_id)
        )
        """
        create_table_if_not_exists("tv_subscriptions", create_tv_subscriptions_sql)

        # Movie Subscriptions
        create_movie_subscriptions_sql = """
        CREATE TABLE IF NOT EXISTS movie_subscriptions (
            user_id TEXT NOT NULL,
            tmdb_id INTEGER NOT NULL,
            title TEXT,
            poster_path TEXT,
            notified_status INTEGER DEFAULT 0 CHECK (notified_status IN (0,1)),
            PRIMARY KEY (user_id, tmdb_id)
        )
        """
        create_table_if_not_exists("movie_subscriptions", create_movie_subscriptions_sql)

        # Tracked Stocks
        create_tracked_stocks_sql = """
        CREATE TABLE IF NOT EXISTS tracked_stocks (
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity REAL, -- Use REAL for floating point numbers
            purchase_price REAL, -- Use REAL for floating point numbers
            currency TEXT,
            PRIMARY KEY (user_id, symbol)
        )
        """
        create_table_if_not_exists("tracked_stocks", create_tracked_stocks_sql)

        # Stock Alerts
        create_stock_alerts_sql = """
        CREATE TABLE IF NOT EXISTS stock_alerts (
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            target_above REAL,
            active_above INTEGER DEFAULT 0 CHECK (active_above IN (0,1)),
            target_below REAL,
            active_below INTEGER DEFAULT 0 CHECK (active_below IN (0,1)),
            dpc_above_target REAL,
            dpc_above_active INTEGER DEFAULT 0 CHECK (dpc_above_active IN (0,1)),
            dpc_below_target REAL,
            dpc_below_active INTEGER DEFAULT 0 CHECK (dpc_below_active IN (0,1)),
            PRIMARY KEY (user_id, symbol)
        )
        """
        create_table_if_not_exists("stock_alerts", create_stock_alerts_sql)

        # User Preferences
        create_user_preferences_sql = """
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT NOT NULL,
            pref_key TEXT NOT NULL,
            pref_value TEXT, -- Use TEXT for JSON
            PRIMARY KEY (user_id, pref_key)
        )
        """
        create_table_if_not_exists("user_preferences", create_user_preferences_sql)

        # Currency Rates
        create_currency_rates_sql = """
        CREATE TABLE IF NOT EXISTS currency_rates (
            currency_pair TEXT PRIMARY KEY,
            rate REAL NOT NULL,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        create_table_if_not_exists("currency_rates", create_currency_rates_sql)

        # Sent Episode Notifications
        create_sent_episode_notifications_sql = """
        CREATE TABLE IF NOT EXISTS sent_episode_notifications (
            user_id TEXT NOT NULL,
            show_tmdb_id INTEGER NOT NULL,
            episode_tmdb_id INTEGER NOT NULL,
            season_number INTEGER,
            episode_number INTEGER,
            notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, show_tmdb_id, episode_tmdb_id),
            FOREIGN KEY (user_id, show_tmdb_id) REFERENCES tv_subscriptions (user_id, show_tmdb_id) ON DELETE CASCADE
        )
        """
        create_table_if_not_exists("sent_episode_notifications", create_sent_episode_notifications_sql)

        # Weather Schedules
        create_weather_schedules_sql = """
        CREATE TABLE IF NOT EXISTS weather_schedules (
            user_id TEXT NOT NULL,
            schedule_time TEXT NOT NULL,
            location TEXT,
            PRIMARY KEY (user_id, schedule_time)
        )
        """
        create_table_if_not_exists("weather_schedules", create_weather_schedules_sql)

        # --- Book Author Subscriptions ---
        create_book_author_subscriptions_sql = """
        CREATE TABLE IF NOT EXISTS book_author_subscriptions (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            author_name TEXT,
            channel_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id, author_id)
        )
        """
        create_table_if_not_exists("book_author_subscriptions", create_book_author_subscriptions_sql)

        create_book_author_seen_works_sql = """
        CREATE TABLE IF NOT EXISTS book_author_seen_works (
            author_id TEXT NOT NULL,
            work_id TEXT NOT NULL,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (author_id, work_id)
        )
        """
        create_table_if_not_exists("book_author_seen_works", create_book_author_seen_works_sql)

        # Per-user tracking for book works (needed to respect per-user DND / delivery semantics).
        create_book_author_user_seen_works_sql = """
        CREATE TABLE IF NOT EXISTS book_author_user_seen_works (
            user_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            work_id TEXT NOT NULL,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, author_id, work_id)
        )
        """
        create_table_if_not_exists("book_author_user_seen_works", create_book_author_user_seen_works_sql)

        # --- Reading Progress / Books ---
        # A "reading item" is a user-specific book/audiobook entry with current progress.
        create_reading_items_sql = """
        CREATE TABLE IF NOT EXISTS reading_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            author TEXT,
            ol_work_id TEXT,
            ol_edition_id TEXT,
            cover_url TEXT,
            format TEXT, -- e.g. paper|ebook|kindle|audio (free-form)
            status TEXT NOT NULL DEFAULT 'reading', -- reading|paused|finished|abandoned
            total_pages INTEGER,
            total_audio_seconds INTEGER,
            current_page INTEGER,
            current_kindle_location INTEGER,
            current_percent REAL,
            current_audio_seconds INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            last_update_at TIMESTAMP
        )
        """
        create_table_if_not_exists("reading_items", create_reading_items_sql)

        # reading_items schema migration for older DBs (ADD COLUMN is cheap/safe).
        try:
            cols = self._execute_query("PRAGMA table_info(reading_items);", fetch_all=True) or []
            col_names = {c.get("name") for c in cols if isinstance(c, dict) and isinstance(c.get("name"), str)}
            if "ol_work_id" not in col_names:
                self._execute_query("ALTER TABLE reading_items ADD COLUMN ol_work_id TEXT;", commit=True)
                logger.info("Column 'ol_work_id' added to reading_items.")
            if "ol_edition_id" not in col_names:
                self._execute_query("ALTER TABLE reading_items ADD COLUMN ol_edition_id TEXT;", commit=True)
                logger.info("Column 'ol_edition_id' added to reading_items.")
            if "cover_url" not in col_names:
                self._execute_query("ALTER TABLE reading_items ADD COLUMN cover_url TEXT;", commit=True)
                logger.info("Column 'cover_url' added to reading_items.")
        except Exception as e:
            logger.error(f"Failed to ensure reading_items columns exist: {e}")

        # Each progress update is logged for history/stats.
        create_reading_updates_sql = """
        CREATE TABLE IF NOT EXISTS reading_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL, -- page|kindle_loc|percent|audio_seconds
            value REAL,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        create_table_if_not_exists("reading_updates", create_reading_updates_sql)

        # --- Games Tracking ---
        # A "game item" is a user-specific backlog entry with optional external metadata.
        create_game_items_sql = """
        CREATE TABLE IF NOT EXISTS game_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'backlog', -- backlog|playing|paused|completed|dropped
            platform TEXT,
            steam_appid INTEGER,
            steam_url TEXT,
            cover_url TEXT,
            release_date TEXT,
            genres TEXT, -- JSON list
            developer TEXT,
            publisher TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            last_update_at TIMESTAMP
        )
        """
        create_table_if_not_exists("game_items", create_game_items_sql)

        # Helpful index for listing by user/status quickly
        try:
            self._execute_query("CREATE INDEX IF NOT EXISTS idx_game_items_user_status ON game_items(user_id, status);", commit=True)
        except Exception as e:
            logger.warning(f"Could not create idx_game_items_user_status: {e}")

        # --- Productivity: To-Dos ---
        create_todo_items_sql = """
        CREATE TABLE IF NOT EXISTS todo_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, -- 0 for DMs / personal scope, else actual guild id
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            is_done INTEGER NOT NULL DEFAULT 0 CHECK (is_done IN (0,1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            done_at TIMESTAMP,
            remind_enabled INTEGER NOT NULL DEFAULT 0 CHECK (remind_enabled IN (0,1)),
            remind_level INTEGER NOT NULL DEFAULT 0,
            next_remind_at TIMESTAMP
        )
        """
        create_table_if_not_exists("todo_items", create_todo_items_sql)
        try:
            self._execute_query("CREATE INDEX IF NOT EXISTS idx_todo_items_user_done ON todo_items(user_id, guild_id, is_done, id);", commit=True)
        except Exception as e:
            logger.warning(f"Could not create idx_todo_items_user_done: {e}")

        # --- Productivity: Habits ---
        create_habits_sql = """
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, -- 0 for DMs / personal scope
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            days_of_week TEXT NOT NULL, -- JSON list of ints: 0=Mon..6=Sun
            due_time_local TEXT NOT NULL DEFAULT '18:00', -- HH:MM interpreted in tz_name
            tz_name TEXT NOT NULL DEFAULT 'Europe/Warsaw', -- IANA tz (CET/CEST), e.g. Europe/Warsaw
            due_time_utc TEXT NOT NULL DEFAULT '18:00', -- legacy: previously interpreted as UTC
            remind_enabled INTEGER NOT NULL DEFAULT 1 CHECK (remind_enabled IN (0,1)),
            remind_profile TEXT NOT NULL DEFAULT 'normal', -- gentle|normal|aggressive|quiet
            snoozed_until TIMESTAMP, -- when set, reminders/due are suppressed until this UTC timestamp
            last_snooze_at TIMESTAMP, -- UTC timestamp of last snooze action
            last_snooze_period TEXT NOT NULL DEFAULT 'week', -- 'week' or 'month'
            remind_level INTEGER NOT NULL DEFAULT 0,
            next_due_at TIMESTAMP, -- computed UTC timestamp for next due occurrence
            next_remind_at TIMESTAMP,
            last_checkin_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        create_table_if_not_exists("habits", create_habits_sql)
        # Habits schema migration for older DBs (ADD COLUMN is cheap/safe).
        try:
            cols = self._execute_query("PRAGMA table_info(habits);", fetch_all=True)
            if cols and isinstance(cols, list):
                col_names = [c.get("name") for c in cols if isinstance(c, dict)]
                if "due_time_local" not in col_names:
                    # Do NOT add a NOT NULL+DEFAULT here, or we'd overwrite existing habits' times.
                    self._execute_query("ALTER TABLE habits ADD COLUMN due_time_local TEXT;", commit=True)
                    # Preserve legacy semantics: copy the old stored value.
                    self._execute_query("UPDATE habits SET due_time_local = due_time_utc WHERE due_time_local IS NULL;", commit=True)
                    logger.info("Column 'due_time_local' added successfully to habits.")
                if "tz_name" not in col_names:
                    self._execute_query("ALTER TABLE habits ADD COLUMN tz_name TEXT;", commit=True)
                    # Preserve legacy semantics: old habits were interpreted as UTC.
                    self._execute_query("UPDATE habits SET tz_name = 'UTC' WHERE tz_name IS NULL;", commit=True)
                    logger.info("Column 'tz_name' added successfully to habits.")
                if "remind_profile" not in col_names:
                    # Safe: SQLite will backfill existing rows with DEFAULT on ADD COLUMN.
                    self._execute_query(
                        "ALTER TABLE habits ADD COLUMN remind_profile TEXT NOT NULL DEFAULT 'normal';",
                        commit=True,
                    )
                    logger.info("Column 'remind_profile' added successfully to habits.")
                if "snoozed_until" not in col_names:
                    self._execute_query("ALTER TABLE habits ADD COLUMN snoozed_until TIMESTAMP;", commit=True)
                    logger.info("Column 'snoozed_until' added successfully to habits.")
                if "last_snooze_at" not in col_names:
                    self._execute_query("ALTER TABLE habits ADD COLUMN last_snooze_at TIMESTAMP;", commit=True)
                    logger.info("Column 'last_snooze_at' added successfully to habits.")
                if "last_snooze_period" not in col_names:
                    self._execute_query(
                        "ALTER TABLE habits ADD COLUMN last_snooze_period TEXT NOT NULL DEFAULT 'week';",
                        commit=True,
                    )
                    logger.info("Column 'last_snooze_period' added successfully to habits.")
        except Exception as e:
            logger.warning(f"Could not apply habits schema migration (due_time_local/tz_name): {e}")
        try:
            self._execute_query("CREATE INDEX IF NOT EXISTS idx_habits_user_due ON habits(user_id, guild_id, next_due_at);", commit=True)
        except Exception as e:
            logger.warning(f"Could not create idx_habits_user_due: {e}")

        create_habit_checkins_sql = """
        CREATE TABLE IF NOT EXISTS habit_checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            checked_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            note TEXT
        )
        """
        create_table_if_not_exists("habit_checkins", create_habit_checkins_sql)
        if not self._execute_query(
            "CREATE INDEX IF NOT EXISTS idx_habit_checkins_habit ON habit_checkins(habit_id, checked_in_at);",
            commit=True,
        ):
            logger.warning("Could not create idx_habit_checkins_habit.")

        # --- Generic Reminders (one-off + repeating) ---
        create_reminders_sql = """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, -- 0 for DMs
            channel_id TEXT NOT NULL, -- 0 for DMs
            user_id TEXT NOT NULL,
            message TEXT NOT NULL,
            trigger_at TIMESTAMP NOT NULL, -- UTC timestamp string "YYYY-MM-DD HH:MM:SS"
            repeat_interval_seconds INTEGER, -- NULL for one-off
            repeat_count INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        create_table_if_not_exists("reminders", create_reminders_sql)
        try:
            self._execute_query("CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(is_active, trigger_at);", commit=True)
            self._execute_query("CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id, is_active, id);", commit=True)
        except Exception as e:
            logger.warning(f"Could not create reminders indexes: {e}")

        logger.info("Database initialization check complete.")

    # -------------------------
    # Productivity: To-Dos
    # -------------------------

    def close(self) -> None:
        """Closes the database connection."""
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
                logger.info("SQLite database connection closed.")
            except sqlite3.Error as e:
                logger.error(f"Error closing SQLite database connection: {e}")

    # --- Book Author Subscriptions ---
