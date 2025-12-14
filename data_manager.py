# data_manager.py
import sqlite3
import os
import json
import logging
import threading
import datetime
from typing import List, Dict, Any, Optional, Union
from config import SQLITE_DB_PATH

logger = logging.getLogger(__name__)

class DataManager:
    def __init__(self) -> None:
        if not SQLITE_DB_PATH:
            logger.error("SQLite database path (SQLITE_DB_PATH) not set in environment variables.")
            raise ValueError("SQLite database path not set.")

        try:
            # Log the database path being used
            logger.info(f"Attempting to connect to database at: {SQLITE_DB_PATH}")

            # Ensure the directory for the SQLite database file exists
            db_dir = os.path.dirname(SQLITE_DB_PATH)
            logger.info(f"Database directory derived as: {db_dir}")
            if db_dir: # Check if db_dir is not empty (i.e., not just a filename in the current dir)
                os.makedirs(db_dir, exist_ok=True)
                logger.info(f"Ensured directory exists: {db_dir}")

            # Connect to SQLite database (creates the file if it doesn't exist)
            # Use check_same_thread=False to allow cross-thread access
            self.conn = sqlite3.connect(SQLITE_DB_PATH, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row # Access columns by name
            # Serialize access because we share one connection across threads (run_in_executor).
            self._lock = threading.RLock()
            logger.info(f"Successfully connected to SQLite database at {SQLITE_DB_PATH}.")
            self._initialize_db()
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to SQLite Database: {e}")
            raise ConnectionError(f"Failed to connect to SQLite Database: {e}")
        except OSError as e: # Catch potential errors from os.makedirs
            logger.error(f"Failed to create directory for SQLite database: {e}")
            raise ConnectionError(f"Failed to create directory for SQLite database: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """Returns the active connection."""
        return self.conn

    def _close_connection(self, connection: sqlite3.Connection) -> None:
        """SQLite connections don't need explicit release like connection pools."""
        pass # No-op for SQLite single connection

    def _execute_query(self, query: str, params: Optional[Dict[str, Any]] = None, fetch_one: bool = False, fetch_all: bool = False, commit: bool = False) -> Any:
        """Executes a given SQL query."""
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
                return cursor # For cases like rowcount or other cursor properties
            except sqlite3.Error as e:
                logger.error(f"Database query error: {e}\nQuery: {query}\nParams: {params}")
                try:
                    conn.rollback()
                except sqlite3.Error as re:
                    logger.error(f"Rollback failed: {re}")
                return False if commit else (None if fetch_one else []) # Consistent error return
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
            try:
                self._execute_query(create_sql, commit=True)
                logger.info(f"Table {table_name} checked/created.")
            except sqlite3.Error as e:
                logger.error(f"Error creating table {table_name}: {e}")
                raise # Re-raise the exception

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
                    try:
                        self._execute_query(alter_query, commit=True)
                        logger.info("Column 'show_tvmaze_id' added successfully.")
                    except sqlite3.Error as e:
                        logger.error(f"Failed to add column 'show_tvmaze_id': {e}")
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
                    try:
                        self._execute_query("ALTER TABLE tracked_stocks ADD COLUMN currency TEXT;", commit=True)
                        logger.info("Column 'currency' added successfully to tracked_stocks.")
                    except sqlite3.Error as e:
                        logger.error(f"Failed to add column 'currency' to tracked_stocks: {e}")
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
            due_time_utc TEXT NOT NULL DEFAULT '18:00', -- HH:MM interpreted as UTC
            remind_enabled INTEGER NOT NULL DEFAULT 1 CHECK (remind_enabled IN (0,1)),
            remind_level INTEGER NOT NULL DEFAULT 0,
            next_due_at TIMESTAMP, -- computed UTC timestamp for next due occurrence
            next_remind_at TIMESTAMP,
            last_checkin_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        create_table_if_not_exists("habits", create_habits_sql)
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
        try:
            self._execute_query("CREATE INDEX IF NOT EXISTS idx_habit_checkins_habit ON habit_checkins(habit_id, checked_in_at);", commit=True)
        except Exception as e:
            logger.warning(f"Could not create idx_habit_checkins_habit: {e}")

        logger.info("Database initialization check complete.")

    # -------------------------
    # Productivity: To-Dos
    # -------------------------
    def create_todo_item(self, guild_id: int, user_id: int, content: str) -> Optional[int]:
        if not content or not str(content).strip():
            return None
        query = """
        INSERT INTO todo_items (guild_id, user_id, content, is_done, remind_enabled, remind_level, next_remind_at)
        VALUES (:guild_id, :user_id, :content, 0, 0, 0, NULL)
        """
        params = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "content": str(content).strip()}
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as e:
                logger.error(f"create_todo_item failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return None
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def list_todo_items(self, guild_id: int, user_id: int, include_done: bool = False, limit: int = 30) -> List[Dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        base = """
        SELECT id, content, is_done, created_at, done_at, remind_enabled, remind_level, next_remind_at
        FROM todo_items
        WHERE guild_id = :guild_id AND user_id = :user_id
        """
        params: Dict[str, Any] = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "limit": limit}
        if not include_done:
            base += " AND is_done = 0"
        base += " ORDER BY is_done ASC, id DESC LIMIT :limit"
        rows = self._execute_query(base, params, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def set_todo_done(self, guild_id: int, user_id: int, todo_id: int, done: bool) -> bool:
        query = """
        UPDATE todo_items
        SET is_done = :done,
            done_at = CASE WHEN :done = 1 THEN CURRENT_TIMESTAMP ELSE NULL END,
            remind_enabled = CASE WHEN :done = 1 THEN 0 ELSE remind_enabled END,
            remind_level = CASE WHEN :done = 1 THEN 0 ELSE remind_level END,
            next_remind_at = CASE WHEN :done = 1 THEN NULL ELSE next_remind_at END
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        params = {
            "done": 1 if done else 0,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(todo_id),
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_todo_done failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def delete_todo_item(self, guild_id: int, user_id: int, todo_id: int) -> bool:
        query = "DELETE FROM todo_items WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id"
        params = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(todo_id)}
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"delete_todo_item failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def set_todo_reminder(self, guild_id: int, user_id: int, todo_id: int, enabled: bool, next_remind_at_utc: Optional[str]) -> bool:
        query = """
        UPDATE todo_items
        SET remind_enabled = :enabled,
            remind_level = CASE WHEN :enabled = 1 THEN remind_level ELSE 0 END,
            next_remind_at = :next_remind_at
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id AND is_done = 0
        """
        params = {
            "enabled": 1 if enabled else 0,
            "next_remind_at": next_remind_at_utc,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(todo_id),
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_todo_reminder failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def bump_todo_reminder(self, guild_id: int, user_id: int, todo_id: int, remind_level: int, next_remind_at_utc: str) -> bool:
        query = """
        UPDATE todo_items
        SET remind_level = :level,
            next_remind_at = :next_remind_at
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id AND is_done = 0 AND remind_enabled = 1
        """
        params = {
            "level": int(remind_level),
            "next_remind_at": next_remind_at_utc,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(todo_id),
        }
        return bool(self._execute_query(query, params, commit=True))

    def list_due_todo_reminders(self, now_utc: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        query = """
        SELECT id, guild_id, user_id, content, remind_level, next_remind_at
        FROM todo_items
        WHERE is_done = 0
          AND remind_enabled = 1
          AND next_remind_at IS NOT NULL
          AND next_remind_at <= :now
        ORDER BY next_remind_at ASC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"now": now_utc, "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    # -------------------------
    # Productivity: Habits
    # -------------------------
    def create_habit(
        self,
        guild_id: int,
        user_id: int,
        name: str,
        days_of_week: List[int],
        due_time_utc: str,
        remind_enabled: bool = True,
        next_due_at_utc: Optional[str] = None,
    ) -> Optional[int]:
        if not name or not str(name).strip():
            return None
        try:
            days_json = json.dumps([int(d) for d in days_of_week])
        except Exception:
            days_json = json.dumps([0, 1, 2, 3, 4])
        query = """
        INSERT INTO habits (guild_id, user_id, name, days_of_week, due_time_utc, remind_enabled, remind_level, next_due_at, next_remind_at)
        VALUES (:guild_id, :user_id, :name, :days, :due_time, :remind_enabled, 0, :next_due_at, NULL)
        """
        params = {
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "name": str(name).strip(),
            "days": days_json,
            "due_time": str(due_time_utc).strip(),
            "remind_enabled": 1 if remind_enabled else 0,
            "next_due_at": next_due_at_utc,
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as e:
                logger.error(f"create_habit failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return None
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def list_habits(self, guild_id: int, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(200, int(limit)))
        query = """
        SELECT id, name, days_of_week, due_time_utc, remind_enabled, remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
        FROM habits
        WHERE guild_id = :guild_id AND user_id = :user_id
        ORDER BY id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def get_habit(self, guild_id: int, user_id: int, habit_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT id, name, days_of_week, due_time_utc, remind_enabled, remind_level, next_due_at, next_remind_at, last_checkin_at, created_at
        FROM habits
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        row = self._execute_query(query, {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(habit_id)}, fetch_one=True)
        return row if isinstance(row, dict) else None

    def delete_habit(self, guild_id: int, user_id: int, habit_id: int) -> bool:
        query = "DELETE FROM habits WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id"
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(habit_id)})
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"delete_habit failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def set_habit_schedule_and_due(
        self,
        guild_id: int,
        user_id: int,
        habit_id: int,
        *,
        days_of_week: Optional[List[int]] = None,
        due_time_utc: Optional[str] = None,
        next_due_at_utc: Optional[str] = None,
        remind_enabled: Optional[bool] = None,
        next_remind_at_utc: Optional[str] = None,
        remind_level: Optional[int] = None,
    ) -> bool:
        set_parts: List[str] = []
        params: Dict[str, Any] = {"guild_id": str(int(guild_id)), "user_id": str(int(user_id)), "id": int(habit_id)}
        if days_of_week is not None:
            try:
                params["days"] = json.dumps([int(d) for d in days_of_week])
            except Exception:
                params["days"] = json.dumps([0, 1, 2, 3, 4])
            set_parts.append("days_of_week = :days")
        if due_time_utc is not None:
            params["due_time"] = str(due_time_utc).strip()
            set_parts.append("due_time_utc = :due_time")
        if next_due_at_utc is not None:
            params["next_due_at"] = next_due_at_utc
            set_parts.append("next_due_at = :next_due_at")
        if remind_enabled is not None:
            params["remind_enabled"] = 1 if remind_enabled else 0
            set_parts.append("remind_enabled = :remind_enabled")
        if next_remind_at_utc is not None:
            params["next_remind_at"] = next_remind_at_utc
            set_parts.append("next_remind_at = :next_remind_at")
        if remind_level is not None:
            params["remind_level"] = int(remind_level)
            set_parts.append("remind_level = :remind_level")
        if not set_parts:
            return False
        query = f"""
        UPDATE habits
        SET {", ".join(set_parts)}
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        return bool(self._execute_query(query, params, commit=True))

    def record_habit_checkin(
        self,
        guild_id: int,
        user_id: int,
        habit_id: int,
        note: Optional[str] = None,
        next_due_at_utc: Optional[str] = None,
    ) -> bool:
        # Insert history row (best-effort) and update habit fields.
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO habit_checkins (habit_id, guild_id, user_id, note)
                    VALUES (:habit_id, :guild_id, :user_id, :note)
                    """,
                    {
                        "habit_id": int(habit_id),
                        "guild_id": str(int(guild_id)),
                        "user_id": str(int(user_id)),
                        "note": note.strip() if isinstance(note, str) and note.strip() else None,
                    },
                )
                cur.execute(
                    """
                    UPDATE habits
                    SET last_checkin_at = CURRENT_TIMESTAMP,
                        remind_level = 0,
                        next_remind_at = NULL,
                        next_due_at = :next_due_at
                    WHERE guild_id = :guild_id AND user_id = :user_id AND id = :habit_id
                    """,
                    {
                        "next_due_at": next_due_at_utc,
                        "guild_id": str(int(guild_id)),
                        "user_id": str(int(user_id)),
                        "habit_id": int(habit_id),
                    },
                )
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"record_habit_checkin failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def list_due_habit_reminders(self, now_utc: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        query = """
        SELECT id, guild_id, user_id, name, remind_level, next_due_at, next_remind_at
        FROM habits
        WHERE remind_enabled = 1
          AND next_due_at IS NOT NULL
          AND next_due_at <= :now
          AND (next_remind_at IS NULL OR next_remind_at <= :now)
        ORDER BY COALESCE(next_remind_at, next_due_at) ASC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"now": now_utc, "limit": limit}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def bump_habit_reminder(self, guild_id: int, user_id: int, habit_id: int, remind_level: int, next_remind_at_utc: str) -> bool:
        query = """
        UPDATE habits
        SET remind_level = :level,
            next_remind_at = :next_remind_at
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id AND remind_enabled = 1
        """
        params = {
            "level": int(remind_level),
            "next_remind_at": next_remind_at_utc,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(habit_id),
        }
        return bool(self._execute_query(query, params, commit=True))

    def set_habit_reminder_enabled(self, guild_id: int, user_id: int, habit_id: int, enabled: bool) -> bool:
        """
        Enables/disables reminders. When disabling, clears next_remind_at and resets remind_level.
        """
        query = """
        UPDATE habits
        SET remind_enabled = :enabled,
            remind_level = CASE WHEN :enabled = 1 THEN remind_level ELSE 0 END,
            next_remind_at = CASE WHEN :enabled = 1 THEN next_remind_at ELSE NULL END
        WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id
        """
        params = {
            "enabled": 1 if enabled else 0,
            "guild_id": str(int(guild_id)),
            "user_id": str(int(user_id)),
            "id": int(habit_id),
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                updated = int(cur.rowcount or 0)
                conn.commit()
                return updated > 0
            except sqlite3.Error as e:
                logger.error(f"set_habit_reminder_enabled failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    # --- TV Show Subscriptions ---
    def add_tv_show_subscription(self, user_id: int, show_tmdb_id: int, show_name: str, poster_path: str, show_tvmaze_id: Optional[int] = None) -> bool:
        user_id_str = str(user_id)
        query = """
        INSERT OR IGNORE INTO tv_subscriptions (user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details, show_tvmaze_id)
        VALUES (:user_id, :show_tmdb_id, :show_name, :poster_path, NULL, :show_tvmaze_id)
        """
        params = {
            "user_id": user_id_str, "show_tmdb_id": show_tmdb_id,
            "show_name": show_name, "poster_path": poster_path,
            "show_tvmaze_id": show_tvmaze_id
        }
        return self._execute_query(query, params, commit=True)

    def update_tv_subscription_tvmaze_id(self, user_id: int, show_tmdb_id: int, show_tvmaze_id: int) -> bool:
        user_id_str = str(user_id)
        query = """
        UPDATE tv_subscriptions
        SET show_tvmaze_id = :show_tvmaze_id
        WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id
        """
        params = {
            "user_id": user_id_str,
            "show_tmdb_id": show_tmdb_id,
            "show_tvmaze_id": show_tvmaze_id
        }
        return self._execute_query(query, params, commit=True)

    def remove_tv_show_subscription(self, user_id: int, show_tmdb_id: int) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM tv_subscriptions WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id"
        params = {"user_id": user_id_str, "show_tmdb_id": show_tmdb_id}
        result = self._execute_query(query, params, commit=True)
        # Check if any row was actually deleted
        # The _execute_query for commit=True returns True on success, not rowcount.
        # To confirm deletion, we'd need to query before/after or check cursor.rowcount before commit.
        # For now, assume success if query executes. A more robust check might be needed.
        return result if isinstance(result, bool) else False


    def get_user_tv_subscriptions(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = "SELECT user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details, show_tvmaze_id FROM tv_subscriptions WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        subscriptions = self._execute_query(query, params, fetch_all=True)
        for sub in subscriptions:
            if sub.get('last_notified_episode_details'):
                try:
                    # SQLite returns TEXT directly, no LOB handling needed
                    details_str = sub['last_notified_episode_details']
                    sub['last_notified_episode_details'] = json.loads(details_str) if details_str else None
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding last_notified_episode_details for user {user_id_str}, show_tmdb_id {sub.get('show_tmdb_id')}: {e}")
                    sub['last_notified_episode_details'] = None
        return subscriptions

    def get_all_tv_subscriptions(self) -> Dict[str, List[Dict[str, Any]]]:
        query = "SELECT user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details, show_tvmaze_id FROM tv_subscriptions"
        subscriptions = self._execute_query(query, fetch_all=True)
        for sub in subscriptions:
            if sub.get('last_notified_episode_details'):
                try:
                    # SQLite returns TEXT directly
                    details_str = sub['last_notified_episode_details']
                    sub['last_notified_episode_details'] = json.loads(details_str) if details_str else None
                except json.JSONDecodeError as e:
                    logger.error(f"Error decoding last_notified_episode_details for show_tmdb_id {sub.get('show_tmdb_id')} in get_all: {e}")
                    sub['last_notified_episode_details'] = None
        # The old method returned a dict keyed by user_id. Let's try to match that.
        result_dict: Dict[str, List[Dict[str, Any]]] = {}
        for sub in subscriptions:
            uid = sub['user_id']
            if uid not in result_dict:
                result_dict[uid] = []
            # Remove user_id from the sub-dictionary as it's now the key
            entry = {k: v for k, v in sub.items() if k != 'user_id'}
            result_dict[uid].append(entry)
        return result_dict


    def update_last_notified_episode_details(self, user_id: int, show_tmdb_id: int, episode_details: Optional[Dict[str, Any]]) -> bool:
        user_id_str = str(user_id)
        # Serialize the episode_details dict to a JSON string for storage
        details_json = json.dumps(episode_details) if episode_details else None
        query = """
        UPDATE tv_subscriptions 
        SET last_notified_episode_details = :details
        WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id
        """
        params = {"details": details_json, "user_id": user_id_str, "show_tmdb_id": show_tmdb_id}
        return self._execute_query(query, params, commit=True)

    # --- Sent Episode Notifications ---
    def add_sent_episode_notification(self, user_id: int, show_tmdb_id: int, episode_tmdb_id: int, season_number: int, episode_number: int) -> bool:
        user_id_str = str(user_id)
        query = """
        INSERT OR IGNORE INTO sent_episode_notifications 
            (user_id, show_tmdb_id, episode_tmdb_id, season_number, episode_number)
        VALUES (:user_id, :show_tmdb_id, :episode_tmdb_id, :season_number, :episode_number)
        """
        params = {
            "user_id": user_id_str,
            "show_tmdb_id": show_tmdb_id,
            "episode_tmdb_id": episode_tmdb_id,
            "season_number": season_number,
            "episode_number": episode_number
        }
        return self._execute_query(query, params, commit=True)

    def has_user_been_notified_for_episode(self, user_id: int, show_tmdb_id: int, episode_tmdb_id: int) -> bool:
        user_id_str = str(user_id)
        query = """
        SELECT 1 FROM sent_episode_notifications
        WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id AND episode_tmdb_id = :episode_tmdb_id
        LIMIT 1
        """
        params = {
            "user_id": user_id_str,
            "show_tmdb_id": show_tmdb_id,
            "episode_tmdb_id": episode_tmdb_id
        }
        result = self._execute_query(query, params, fetch_one=True)
        return bool(result) # True if a record is found, False otherwise

    def has_user_been_notified_for_episode_by_number(self, user_id: int, show_tmdb_id: int, season_number: int, episode_number: int) -> bool:
        """
        Checks if a user has been notified for an episode based on season/episode number.
        Useful for robustness across different data providers (TMDB vs TVMaze) where IDs might differ.
        """
        user_id_str = str(user_id)
        query = """
        SELECT 1 FROM sent_episode_notifications
        WHERE user_id = :user_id AND show_tmdb_id = :show_tmdb_id 
          AND season_number = :season_number AND episode_number = :episode_number
        LIMIT 1
        """
        params = {
            "user_id": user_id_str,
            "show_tmdb_id": show_tmdb_id,
            "season_number": season_number,
            "episode_number": episode_number
        }
        result = self._execute_query(query, params, fetch_one=True)
        return bool(result)

    # --- Movie Subscriptions ---
    def add_movie_subscription(self, user_id: int, tmdb_id: int, title: str, poster_path: str) -> bool:
        user_id_str = str(user_id)
        query = """
        INSERT OR IGNORE INTO movie_subscriptions (user_id, tmdb_id, title, poster_path, notified_status)
        VALUES (:user_id, :tmdb_id, :title, :poster_path, 0)
        """
        params = {"user_id": user_id_str, "tmdb_id": tmdb_id, "title": title, "poster_path": poster_path}
        return self._execute_query(query, params, commit=True)

    def remove_movie_subscription(self, user_id: int, tmdb_id: int) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM movie_subscriptions WHERE user_id = :user_id AND tmdb_id = :tmdb_id"
        params = {"user_id": user_id_str, "tmdb_id": tmdb_id}
        return self._execute_query(query, params, commit=True)

    def get_user_movie_subscriptions(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = "SELECT user_id, tmdb_id, title, poster_path, notified_status FROM movie_subscriptions WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        subscriptions = self._execute_query(query, params, fetch_all=True)
        for sub in subscriptions: # Convert 0/1 to False/True
            sub['notified_status'] = bool(sub.get('notified_status', 0))
        return subscriptions

    def get_all_movie_subscriptions(self) -> Dict[str, List[Dict[str, Any]]]:
        query = "SELECT user_id, tmdb_id, title, poster_path, notified_status FROM movie_subscriptions"
        subscriptions = self._execute_query(query, fetch_all=True)
        result_dict: Dict[str, List[Dict[str, Any]]] = {}
        for sub in subscriptions:
            sub['notified_status'] = bool(sub.get('notified_status', 0))
            uid = sub['user_id']
            if uid not in result_dict:
                result_dict[uid] = []
            entry = {k: v for k, v in sub.items() if k != 'user_id'}
            result_dict[uid].append(entry)
        return result_dict


    def update_movie_notified_status(self, user_id: int, tmdb_id: int, status: bool) -> bool:
        user_id_str = str(user_id)
        query = "UPDATE movie_subscriptions SET notified_status = :status WHERE user_id = :user_id AND tmdb_id = :tmdb_id"
        params = {"status": 1 if status else 0, "user_id": user_id_str, "tmdb_id": tmdb_id}
        return self._execute_query(query, params, commit=True)

    # --- Tracked Stocks ---
    def add_tracked_stock(self, user_id: int, stock_symbol: str, quantity: Optional[float] = None, purchase_price: Optional[float] = None, currency: Optional[str] = None) -> bool:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        currency_upper = currency.upper() if currency else None
        
        # MERGE can handle insert or update logic
        query = """
        INSERT INTO tracked_stocks (user_id, symbol, quantity, purchase_price, currency)
        VALUES (:user_id, :symbol, :quantity, :purchase_price, :currency)
        ON CONFLICT(user_id, symbol) DO UPDATE SET
            quantity = COALESCE(:quantity, quantity), -- Use COALESCE for NVL equivalent
            purchase_price = COALESCE(:purchase_price, purchase_price),
            currency = COALESCE(:currency, currency)
        """
        # Note: SQLite's ON CONFLICT DO UPDATE SET updates ALL listed fields if there's a conflict.
        # The COALESCE function handles the case where the input parameter is None,
        # keeping the existing value in the database. This simplifies the logic
        # compared to the original Oracle MERGE with its complex WHERE clause.
        # This also aligns with the desired behavior: if quantity/price is None,
        # it doesn't overwrite the existing value. If the stock is new, it inserts
        # with NULLs if quantity/price are None, which is acceptable.

        params = {"user_id": user_id_str, "symbol": symbol_upper, "quantity": quantity, "purchase_price": purchase_price, "currency": currency_upper}
        
        # The original Oracle logic had a check for new stocks requiring both quantity and price.
        # The SQLite UPSERT doesn't enforce this at the DB level.
        # We can add a Python-side check if strict adherence to that specific behavior is needed,
        # but the UPSERT with COALESCE provides a more flexible and common pattern.
        # For now, we'll rely on the UPSERT behavior.

        return self._execute_query(query, params, commit=True)


    def get_user_tracked_stocks_for_symbol(self, user_id_str: str, symbol_upper: str) -> Optional[Dict[str, Any]]:
        # Helper for add_tracked_stock
        query = "SELECT symbol, quantity, purchase_price, currency FROM tracked_stocks WHERE user_id = :user_id AND symbol = :symbol"
        return self._execute_query(query, {"user_id": user_id_str, "symbol": symbol_upper}, fetch_one=True)


    def remove_tracked_stock(self, user_id: int, stock_symbol: str) -> bool:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        query = "DELETE FROM tracked_stocks WHERE user_id = :user_id AND symbol = :symbol"
        params = {"user_id": user_id_str, "symbol": symbol_upper}
        return self._execute_query(query, params, commit=True)

    def get_user_tracked_stocks(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = "SELECT symbol, quantity, purchase_price, currency FROM tracked_stocks WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        stocks = self._execute_query(query, params, fetch_all=True)
        # Ensure numeric types are float if not None
        for stock in stocks:
            if stock.get("quantity") is not None:
                stock["quantity"] = float(stock["quantity"])
            if stock.get("purchase_price") is not None:
                stock["purchase_price"] = float(stock["purchase_price"])
        return stocks

    # --- Stock Alerts ---
    def add_stock_alert(self, user_id: int, stock_symbol: str,
                        target_above: Optional[Union[float, str]] = None, target_below: Optional[Union[float, str]] = None,
                        dpc_above_target: Optional[Union[float, str]] = None, dpc_below_target: Optional[Union[float, str]] = None,
                        clear_above: bool = False, clear_below: bool = False,
                        clear_dpc_above: bool = False, clear_dpc_below: bool = False) -> bool:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()

        current_alert = self.get_stock_alert(user_id, stock_symbol) or {} # Ensure it's a dict

        update_data = {
            "target_above": current_alert.get("target_above"), "active_above": current_alert.get("active_above", False),
            "target_below": current_alert.get("target_below"), "active_below": current_alert.get("active_below", False),
            "dpc_above_target": current_alert.get("dpc_above_target"), "dpc_above_active": current_alert.get("dpc_above_active", False),
            "dpc_below_target": current_alert.get("dpc_below_target"), "dpc_below_active": current_alert.get("dpc_below_active", False),
        }
        updated = False

        if clear_above:
            if update_data["target_above"] is not None: update_data["target_above"] = None; update_data["active_above"] = False; updated = True
        elif target_above is not None:
            price = float(target_above)
            if update_data["target_above"] != price or not update_data["active_above"]: update_data["target_above"] = price; update_data["active_above"] = True; updated = True
        
        if clear_below:
            if update_data["target_below"] is not None: update_data["target_below"] = None; update_data["active_below"] = False; updated = True
        elif target_below is not None:
            price = float(target_below)
            if update_data["target_below"] != price or not update_data["active_below"]: update_data["target_below"] = price; update_data["active_below"] = True; updated = True

        if clear_dpc_above:
            if update_data["dpc_above_target"] is not None: update_data["dpc_above_target"] = None; update_data["dpc_above_active"] = False; updated = True
        elif dpc_above_target is not None:
            percent = float(dpc_above_target)
            if update_data["dpc_above_target"] != percent or not update_data["dpc_above_active"]: update_data["dpc_above_target"] = percent; update_data["dpc_above_active"] = True; updated = True

        if clear_dpc_below:
            if update_data["dpc_below_target"] is not None: update_data["dpc_below_target"] = None; update_data["dpc_below_active"] = False; updated = True
        elif dpc_below_target is not None:
            percent = float(dpc_below_target)
            if update_data["dpc_below_target"] != percent or not update_data["dpc_below_active"]: update_data["dpc_below_target"] = percent; update_data["dpc_below_active"] = True; updated = True
        
        if not updated:
            return False # No changes made

        # Check if all targets are None, then delete row
        all_targets_none = all([
            update_data["target_above"] is None, update_data["target_below"] is None,
            update_data["dpc_above_target"] is None, update_data["dpc_below_target"] is None
        ])

        if all_targets_none:
            query_delete = "DELETE FROM stock_alerts WHERE user_id = :user_id AND symbol = :symbol"
            params_delete = {"user_id": user_id_str, "symbol": symbol_upper}
            return self._execute_query(query_delete, params_delete, commit=True)
        else:
            # Upsert logic
            query_upsert = """
            INSERT INTO stock_alerts (user_id, symbol,
                                      target_above, active_above,
                                      target_below, active_below,
                                      dpc_above_target, dpc_above_active,
                                      dpc_below_target, dpc_below_active)
            VALUES (:user_id, :symbol,
                    :target_above, :active_above,
                    :target_below, :active_below,
                    :dpc_above_target, :dpc_above_active,
                    :dpc_below_target, :dpc_below_active)
            ON CONFLICT(user_id, symbol) DO UPDATE SET
                target_above = excluded.target_above,
                active_above = excluded.active_above,
                target_below = excluded.target_below,
                active_below = excluded.active_below,
                dpc_above_target = excluded.dpc_above_target,
                dpc_above_active = excluded.dpc_above_active,
                dpc_below_target = excluded.dpc_below_target,
                dpc_below_active = excluded.dpc_below_active
            """
            params_upsert = {
                "user_id": user_id_str, "symbol": symbol_upper,
                "target_above": update_data["target_above"], "active_above": 1 if update_data["active_above"] else 0,
                "target_below": update_data["target_below"], "active_below": 1 if update_data["active_below"] else 0,
                "dpc_above_target": update_data["dpc_above_target"], "dpc_above_active": 1 if update_data["dpc_above_active"] else 0,
                "dpc_below_target": update_data["dpc_below_target"], "dpc_below_active": 1 if update_data["dpc_below_active"] else 0,
            }
            return self._execute_query(query_upsert, params_upsert, commit=True)


    def get_stock_alert(self, user_id: int, stock_symbol: str) -> Optional[Dict[str, Any]]:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        query = """
        SELECT target_above, active_above, target_below, active_below,
               dpc_above_target, dpc_above_active, dpc_below_target, dpc_below_active
        FROM stock_alerts WHERE user_id = :user_id AND symbol = :symbol
        """
        params = {"user_id": user_id_str, "symbol": symbol_upper}
        alert = self._execute_query(query, params, fetch_one=True)
        if alert:
            for key in ['active_above', 'active_below', 'dpc_above_active', 'dpc_below_active']:
                if key in alert: alert[key] = bool(alert[key])
        return alert

    def deactivate_stock_alert_target(self, user_id: int, stock_symbol: str, direction: str) -> bool:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        
        field_to_deactivate = None
        if direction == "above": field_to_deactivate = "active_above"
        elif direction == "below": field_to_deactivate = "active_below"
        elif direction == "dpc_above": field_to_deactivate = "dpc_above_active"
        elif direction == "dpc_below": field_to_deactivate = "dpc_below_active"
        
        if not field_to_deactivate: return False
            
        query = f"UPDATE stock_alerts SET {field_to_deactivate} = 0 WHERE user_id = :user_id AND symbol = :symbol AND {field_to_deactivate} = 1"
        params = {"user_id": user_id_str, "symbol": symbol_upper}
        
        # We need to check if a row was actually updated.
        # _execute_query with commit=True returns True on successful execution, not if rows were affected.
        # A more complex approach would be needed if strict "changed" status is required.
        # For now, assume if it ran, it's fine.
        return self._execute_query(query, params, commit=True)

    def get_user_all_stock_alerts(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = """
        SELECT symbol, target_above, active_above, target_below, active_below,
               dpc_above_target, dpc_above_active, dpc_below_target, dpc_below_active
        FROM stock_alerts WHERE user_id = :user_id
        """
        params = {"user_id": user_id_str}
        alerts = self._execute_query(query, params, fetch_all=True)
        for alert in alerts:
            for key in ['active_above', 'active_below', 'dpc_above_active', 'dpc_below_active']:
                if key in alert: alert[key] = bool(alert[key])
        return alerts

    def get_all_active_alerts_for_monitoring(self) -> Dict[str, Dict[str, Any]]:
        query = """
        SELECT user_id, symbol, target_above, active_above, target_below, active_below,
               dpc_above_target, dpc_above_active, dpc_below_target, dpc_below_active
        FROM stock_alerts
        WHERE active_above = 1 OR active_below = 1 OR dpc_above_active = 1 OR dpc_below_active = 1
        """
        alerts_list = self._execute_query(query, fetch_all=True)
        
        active_alerts_to_monitor: Dict[str, Dict[str, Any]] = {}
        for alert_row in alerts_list:
            uid = alert_row['user_id']
            symbol = alert_row['symbol']
            if uid not in active_alerts_to_monitor:
                active_alerts_to_monitor[uid] = {}
            
            alert_details = {k: v for k, v in alert_row.items() if k not in ['user_id', 'symbol']}
            for key in ['active_above', 'active_below', 'dpc_above_active', 'dpc_below_active']:
                 if key in alert_details: alert_details[key] = bool(alert_details[key])
            active_alerts_to_monitor[uid][symbol] = alert_details
            
        return active_alerts_to_monitor

    # --- User Preferences ---
    def get_user_preference(self, user_id: int, key: str, default: Any = None) -> Any:
        user_id_str = str(user_id)
        query = "SELECT pref_value FROM user_preferences WHERE user_id = :user_id AND pref_key = :key"
        params = {"user_id": user_id_str, "key": key}
        result = self._execute_query(query, params, fetch_one=True)
        if result and result.get('pref_value'):
            try:
                value_str = result['pref_value']
                # SQLite returns TEXT directly, no LOB handling needed
                return json.loads(value_str)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding preference value for user {user_id_str}, key {key}: {e}")
                return default
        return default

    def set_user_preference(self, user_id: int, key: str, value: Any) -> bool:
        user_id_str = str(user_id)
        value_json = json.dumps(value)
        query = """
        INSERT INTO user_preferences (user_id, pref_key, pref_value)
        VALUES (:user_id, :key, :value_json)
        ON CONFLICT(user_id, pref_key) DO UPDATE SET
            pref_value = :value_json
        """
        params = {"user_id": user_id_str, "key": key, "value_json": value_json}
        return self._execute_query(query, params, commit=True)

    def delete_user_preference(self, user_id: int, key: str) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM user_preferences WHERE user_id = :user_id AND pref_key = :key"
        params = {"user_id": user_id_str, "key": key}
        return self._execute_query(query, params, commit=True)

    def get_user_all_preferences(self, user_id: int) -> Dict[str, Any]:
        user_id_str = str(user_id)
        query = "SELECT pref_key, pref_value FROM user_preferences WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        prefs_list = self._execute_query(query, params, fetch_all=True)
        
        user_prefs = {}
        for pref_row in prefs_list:
            key = pref_row['pref_key']
            try:
                value_str = pref_row['pref_value']
                # SQLite returns TEXT directly
                user_prefs[key] = json.loads(value_str)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding preference value for user {user_id_str}, key {key} in get_all_preferences: {e}")
                user_prefs[key] = None # Or some default error indicator
        return user_prefs

    def list_users_with_preference(self, key: str) -> List[Dict[str, Any]]:
        """
        Returns rows of {user_id, pref_value} for a given preference key.
        """
        query = "SELECT user_id, pref_value FROM user_preferences WHERE pref_key = :key"
        rows = self._execute_query(query, {"key": key}, fetch_all=True)
        out: List[Dict[str, Any]] = []
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            uid = r.get("user_id")
            pv = r.get("pref_value")
            if not isinstance(uid, str):
                continue
            # Best-effort JSON decode; keep raw string if invalid
            val: Any
            if isinstance(pv, str) and pv:
                try:
                    val = json.loads(pv)
                except json.JSONDecodeError:
                    val = pv
            else:
                val = None
            out.append({"user_id": uid, "value": val})
        return out

    # --- Currency Rates ---
    def update_currency_rate(self, currency_pair: str, rate: float) -> bool:
        query = """
        INSERT INTO currency_rates (currency_pair, rate, last_updated)
        VALUES (:pair, :rate, CURRENT_TIMESTAMP)
        ON CONFLICT(currency_pair) DO UPDATE SET
            rate = :rate,
            last_updated = CURRENT_TIMESTAMP
        """
        params = {"pair": currency_pair, "rate": rate}
        return self._execute_query(query, params, commit=True)

    def get_currency_rate(self, currency_pair: str) -> Optional[float]:
        query = "SELECT rate FROM currency_rates WHERE currency_pair = :pair"
        params = {"pair": currency_pair}
        result = self._execute_query(query, params, fetch_one=True)
        if result:
            return float(result['rate'])
        return None

    # --- Weather Schedules ---
    def add_weather_schedule(self, user_id: int, schedule_time: str, location: Optional[str] = None) -> bool:
        user_id_str = str(user_id)
        query = """
        INSERT INTO weather_schedules (user_id, schedule_time, location)
        VALUES (:user_id, :time, :location)
        ON CONFLICT(user_id, schedule_time) DO UPDATE SET
            location = :location
        """
        params = {"user_id": user_id_str, "time": schedule_time, "location": location}
        return self._execute_query(query, params, commit=True)

    def remove_weather_schedule(self, user_id: int, schedule_time: str) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM weather_schedules WHERE user_id = :user_id AND schedule_time = :time"
        params = {"user_id": user_id_str, "time": schedule_time}
        return self._execute_query(query, params, commit=True)

    def get_user_weather_schedules(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = "SELECT schedule_time, location FROM weather_schedules WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        return self._execute_query(query, params, fetch_all=True)

    def get_weather_schedules_for_time(self, schedule_time: str) -> List[Dict[str, Any]]:
        query = "SELECT user_id, location FROM weather_schedules WHERE schedule_time = :time"
        params = {"time": schedule_time}
        return self._execute_query(query, params, fetch_all=True)

    def close(self) -> None:
        """Closes the database connection."""
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
                logger.info("SQLite database connection closed.")
            except sqlite3.Error as e:
                logger.error(f"Error closing SQLite database connection: {e}")

    # --- Book Author Subscriptions ---
    def add_book_author_subscription(
        self,
        guild_id: int,
        user_id: int,
        author_id: str,
        author_name: Optional[str] = None,
        channel_id: Optional[int] = None,
    ) -> bool:
        query = """
        INSERT OR IGNORE INTO book_author_subscriptions
            (guild_id, user_id, author_id, author_name, channel_id)
        VALUES (:guild_id, :user_id, :author_id, :author_name, :channel_id)
        """
        params = {
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "author_id": author_id,
            "author_name": author_name,
            "channel_id": str(channel_id) if channel_id is not None else None,
        }
        return self._execute_query(query, params, commit=True)

    def remove_book_author_subscription(self, guild_id: int, user_id: int, author_id: str) -> bool:
        """
        Deletes a subscription and returns True only if a row was actually removed.
        """
        query = """
        DELETE FROM book_author_subscriptions
        WHERE guild_id = :guild_id AND user_id = :user_id AND author_id = :author_id
        """
        params = {"guild_id": str(guild_id), "user_id": str(user_id), "author_id": author_id}
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"remove_book_author_subscription failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def update_book_author_subscription_name(self, guild_id: int, user_id: int, author_id: str, author_name: str) -> bool:
        """
        Updates the stored author_name for an existing subscription.
        """
        query = """
        UPDATE book_author_subscriptions
        SET author_name = :author_name
        WHERE guild_id = :guild_id AND user_id = :user_id AND author_id = :author_id
        """
        params = {
            "author_name": author_name,
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            "author_id": author_id,
        }
        return self._execute_query(query, params, commit=True)

    def get_user_book_author_subscriptions(self, guild_id: int, user_id: int) -> List[Dict[str, Any]]:
        query = """
        SELECT author_id, author_name, channel_id, created_at
        FROM book_author_subscriptions
        WHERE guild_id = :guild_id AND user_id = :user_id
        ORDER BY author_name COLLATE NOCASE
        """
        params = {"guild_id": str(guild_id), "user_id": str(user_id)}
        return self._execute_query(query, params, fetch_all=True)

    def get_all_book_author_subscriptions(self) -> List[Dict[str, Any]]:
        query = """
        SELECT guild_id, user_id, author_id, author_name, channel_id, created_at
        FROM book_author_subscriptions
        """
        return self._execute_query(query, fetch_all=True)

    def get_book_author_subscriptions_for_author(self, author_id: str) -> List[Dict[str, Any]]:
        query = """
        SELECT guild_id, user_id, author_id, author_name, channel_id
        FROM book_author_subscriptions
        WHERE author_id = :author_id
        """
        return self._execute_query(query, {"author_id": author_id}, fetch_all=True)

    def get_seen_work_ids_for_author(self, author_id: str) -> List[str]:
        query = "SELECT work_id FROM book_author_seen_works WHERE author_id = :author_id"
        rows = self._execute_query(query, {"author_id": author_id}, fetch_all=True)
        out: List[str] = []
        for r in rows:
            wid = r.get("work_id")
            if isinstance(wid, str):
                out.append(wid)
        return out

    def mark_author_work_seen(self, author_id: str, work_id: str) -> bool:
        query = """
        INSERT OR IGNORE INTO book_author_seen_works (author_id, work_id)
        VALUES (:author_id, :work_id)
        """
        return self._execute_query(query, {"author_id": author_id, "work_id": work_id}, commit=True)

    def mark_author_works_seen(self, author_id: str, work_ids: List[str]) -> bool:
        """
        Best-effort bulk insert. Returns True if the operation completes.
        """
        if not work_ids:
            return True
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.executemany(
                    "INSERT OR IGNORE INTO book_author_seen_works (author_id, work_id) VALUES (?, ?)",
                    [(author_id, wid) for wid in work_ids],
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                logger.error(f"Bulk mark_author_works_seen failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def get_seen_work_ids_for_user_author(self, user_id: int, author_id: str) -> List[str]:
        query = """
        SELECT work_id
        FROM book_author_user_seen_works
        WHERE user_id = :user_id AND author_id = :author_id
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "author_id": author_id}, fetch_all=True)
        out: List[str] = []
        for r in rows:
            wid = r.get("work_id")
            if isinstance(wid, str):
                out.append(wid)
        return out

    def mark_user_author_work_seen(self, user_id: int, author_id: str, work_id: str) -> bool:
        query = """
        INSERT OR IGNORE INTO book_author_user_seen_works (user_id, author_id, work_id)
        VALUES (:user_id, :author_id, :work_id)
        """
        params = {"user_id": str(user_id), "author_id": author_id, "work_id": work_id}
        return self._execute_query(query, params, commit=True)

    def mark_user_author_works_seen(self, user_id: int, author_id: str, work_ids: List[str]) -> bool:
        """
        Best-effort bulk insert. Returns True if the operation completes.
        """
        if not work_ids:
            return True
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.executemany(
                    "INSERT OR IGNORE INTO book_author_user_seen_works (user_id, author_id, work_id) VALUES (?, ?, ?)",
                    [(str(user_id), author_id, wid) for wid in work_ids],
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                logger.error(f"Bulk mark_user_author_works_seen failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    # --- Reading Progress ---
    def create_reading_item(
        self,
        user_id: int,
        title: str,
        author: Optional[str] = None,
        ol_work_id: Optional[str] = None,
        ol_edition_id: Optional[str] = None,
        cover_url: Optional[str] = None,
        format: Optional[str] = None,
        total_pages: Optional[int] = None,
        total_audio_seconds: Optional[int] = None,
    ) -> Optional[int]:
        """
        Creates a new reading item and returns its ID.
        """
        if not title or not str(title).strip():
            return None
        query = """
        INSERT INTO reading_items
            (user_id, title, author, ol_work_id, ol_edition_id, cover_url, format, total_pages, total_audio_seconds, status)
        VALUES
            (:user_id, :title, :author, :ol_work_id, :ol_edition_id, :cover_url, :format, :total_pages, :total_audio_seconds, 'reading')
        """
        params = {
            "user_id": str(user_id),
            "title": title.strip(),
            "author": author.strip() if isinstance(author, str) and author.strip() else None,
            "ol_work_id": ol_work_id.strip() if isinstance(ol_work_id, str) and ol_work_id.strip() else None,
            "ol_edition_id": ol_edition_id.strip() if isinstance(ol_edition_id, str) and ol_edition_id.strip() else None,
            "cover_url": cover_url.strip() if isinstance(cover_url, str) and cover_url.strip() else None,
            "format": format.strip() if isinstance(format, str) and format.strip() else None,
            "total_pages": int(total_pages) if total_pages is not None else None,
            "total_audio_seconds": int(total_audio_seconds) if total_audio_seconds is not None else None,
        }
        conn = self._get_connection()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(query, params)
            conn.commit()
            return int(cur.lastrowid)
        except sqlite3.Error as e:
            logger.error(f"create_reading_item failed: {e}")
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return None
        finally:
            try:
                if cur:
                    cur.close()
            except Exception:
                pass

    def get_reading_item(self, user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT *
        FROM reading_items
        WHERE user_id = :user_id AND id = :item_id
        """
        row = self._execute_query(query, {"user_id": str(user_id), "item_id": int(item_id)}, fetch_one=True)
        return row if isinstance(row, dict) else None

    def list_reading_items(
        self,
        user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        statuses = statuses or ["reading", "paused"]
        placeholders = ", ".join([f":s{i}" for i in range(len(statuses))]) if statuses else "'reading'"
        query = f"""
        SELECT *
        FROM reading_items
        WHERE user_id = :user_id AND status IN ({placeholders})
        ORDER BY COALESCE(last_update_at, created_at) DESC, id DESC
        LIMIT :limit
        """
        params: Dict[str, Any] = {"user_id": str(user_id), "limit": int(limit)}
        for i, s in enumerate(statuses):
            params[f"s{i}"] = s
        rows = self._execute_query(query, params, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def set_current_reading_item_id(self, user_id: int, item_id: Optional[int]) -> bool:
        """
        Persists the user's current reading item id in user_preferences.
        """
        if item_id is None:
            return self.delete_user_preference(user_id, "reading_current_item_id")
        return self.set_user_preference(user_id, "reading_current_item_id", int(item_id))

    def get_current_reading_item_id(self, user_id: int) -> Optional[int]:
        item_id = self.get_user_preference(user_id, "reading_current_item_id", None)
        if item_id is None:
            return None
        try:
            return int(item_id)
        except (TypeError, ValueError):
            return None

    def get_current_reading_item(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Returns the current reading item (preference-backed). Falls back to latest active.
        """
        preferred_id = self.get_current_reading_item_id(user_id)
        if preferred_id is not None:
            item = self.get_reading_item(user_id, preferred_id)
            if item:
                return item

        # fallback: most recently updated active item
        items = self.list_reading_items(user_id, statuses=["reading", "paused"], limit=1)
        return items[0] if items else None

    def _insert_reading_update(
        self,
        user_id: int,
        item_id: int,
        kind: str,
        value: Optional[float] = None,
        note: Optional[str] = None,
    ) -> bool:
        query = """
        INSERT INTO reading_updates (item_id, user_id, kind, value, note)
        VALUES (:item_id, :user_id, :kind, :value, :note)
        """
        params = {
            "item_id": int(item_id),
            "user_id": str(user_id),
            "kind": kind,
            "value": value,
            "note": note.strip() if isinstance(note, str) and note.strip() else None,
        }
        return bool(self._execute_query(query, params, commit=True))

    def update_reading_progress(
        self,
        user_id: int,
        item_id: int,
        *,
        page: Optional[int] = None,
        pages_delta: Optional[int] = None,
        kindle_loc: Optional[int] = None,
        percent: Optional[float] = None,
        audio_seconds: Optional[int] = None,
        audio_delta_seconds: Optional[int] = None,
        note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Applies progress updates to the reading_items row and logs them into reading_updates.
        Returns the updated item dict, or None on failure.
        """
        item = self.get_reading_item(user_id, item_id)
        if not item:
            return None

        updates: Dict[str, Any] = {}
        log_entries: List[tuple[str, Optional[float]]] = []
        delta_entries: List[tuple[str, Optional[float]]] = []

        # Capture "before" values for deltas/stats
        try:
            prev_page = int(item.get("current_page") or 0)
        except (TypeError, ValueError):
            prev_page = 0
        try:
            prev_audio = int(item.get("current_audio_seconds") or 0)
        except (TypeError, ValueError):
            prev_audio = 0

        if pages_delta is not None:
            try:
                current = int(item.get("current_page") or 0)
                page = current + int(pages_delta)
            except (TypeError, ValueError):
                page = None

        if audio_delta_seconds is not None:
            try:
                current = int(item.get("current_audio_seconds") or 0)
                audio_seconds = current + int(audio_delta_seconds)
            except (TypeError, ValueError):
                audio_seconds = None

        if page is not None:
            try:
                page_i = max(0, int(page))
                updates["current_page"] = page_i
                log_entries.append(("page", float(page_i)))
            except (TypeError, ValueError):
                pass

        if kindle_loc is not None:
            try:
                kl = max(0, int(kindle_loc))
                updates["current_kindle_location"] = kl
                log_entries.append(("kindle_loc", float(kl)))
            except (TypeError, ValueError):
                pass

        if percent is not None:
            try:
                p = float(percent)
                # treat values like 0.42 as 42%
                if 0.0 <= p <= 1.0:
                    p *= 100.0
                p = max(0.0, min(100.0, p))
                updates["current_percent"] = p
                log_entries.append(("percent", float(p)))

                # If total_pages is known, also keep current_page in sync when the user updates by percent.
                # Do not override an explicit page/pages_delta update in the same call.
                if page is None and pages_delta is None and "current_page" not in updates:
                    try:
                        total_pages = int(item.get("total_pages")) if item.get("total_pages") is not None else None
                    except (TypeError, ValueError):
                        total_pages = None
                    if total_pages is not None and total_pages > 0:
                        # Round to nearest page; clamp within [0, total_pages]
                        derived_page = int(round((p / 100.0) * float(total_pages)))
                        derived_page = max(0, min(int(total_pages), derived_page))
                        updates["current_page"] = derived_page
                        log_entries.append(("page", float(derived_page)))
            except (TypeError, ValueError):
                pass

        if audio_seconds is not None:
            try:
                a = max(0, int(audio_seconds))
                updates["current_audio_seconds"] = a
                log_entries.append(("audio_seconds", float(a)))
            except (TypeError, ValueError):
                pass

        if not updates and not (isinstance(note, str) and note.strip()):
            return item

        # Deltas for stats (always non-negative)
        if "current_page" in updates:
            try:
                delta = int(updates["current_page"]) - int(prev_page)
                if delta > 0:
                    delta_entries.append(("pages_delta", float(delta)))
            except (TypeError, ValueError):
                pass
        if "current_audio_seconds" in updates:
            try:
                delta = int(updates["current_audio_seconds"]) - int(prev_audio)
                if delta > 0:
                    delta_entries.append(("audio_delta_seconds", float(delta)))
            except (TypeError, ValueError):
                pass

        # Update item row (best-effort; note-only updates should still touch last_update_at)
        set_parts = []
        params: Dict[str, Any] = {"user_id": str(user_id), "item_id": int(item_id)}
        for k, v in updates.items():
            set_parts.append(f"{k} = :{k}")
            params[k] = v

        set_parts.append("last_update_at = CURRENT_TIMESTAMP")
        # If user updates progress on an item, assume it's active again
        if item.get("status") in ("paused", "abandoned"):
            set_parts.append("status = 'reading'")

        query = f"""
        UPDATE reading_items
        SET {", ".join(set_parts)}
        WHERE user_id = :user_id AND id = :item_id
        """
        ok = self._execute_query(query, params, commit=True)
        if not ok:
            return None

        # Log updates
        for kind, value in log_entries:
            self._insert_reading_update(user_id, item_id, kind, value=value, note=note)

        # Log deltas for stats (do not attach note to avoid noisy history)
        for kind, value in delta_entries:
            self._insert_reading_update(user_id, item_id, kind, value=value, note=None)

        # Note-only entry
        if not log_entries and isinstance(note, str) and note.strip():
            self._insert_reading_update(user_id, item_id, "note", value=None, note=note)

        updated_item = self.get_reading_item(user_id, item_id)
        if not updated_item:
            return None

        # Auto-finish detection
        try:
            status = str(updated_item.get("status") or "reading")
        except Exception:
            status = "reading"
        if status != "finished":
            try:
                total_pages = int(updated_item.get("total_pages")) if updated_item.get("total_pages") is not None else None
            except (TypeError, ValueError):
                total_pages = None
            try:
                total_audio = int(updated_item.get("total_audio_seconds")) if updated_item.get("total_audio_seconds") is not None else None
            except (TypeError, ValueError):
                total_audio = None
            try:
                cur_page = int(updated_item.get("current_page") or 0)
            except (TypeError, ValueError):
                cur_page = 0
            try:
                cur_audio = int(updated_item.get("current_audio_seconds") or 0)
            except (TypeError, ValueError):
                cur_audio = 0

            should_finish = False
            if total_pages is not None and total_pages > 0 and cur_page >= total_pages:
                should_finish = True
            if total_audio is not None and total_audio > 0 and cur_audio >= total_audio:
                should_finish = True

            if should_finish:
                # Will also append a "finished" update entry
                self.finish_reading_item(user_id, item_id)
                updated_item = self.get_reading_item(user_id, item_id) or updated_item

        return updated_item

    def finish_reading_item(self, user_id: int, item_id: int) -> bool:
        query = """
        UPDATE reading_items
        SET status = 'finished',
            finished_at = CURRENT_TIMESTAMP,
            last_update_at = CURRENT_TIMESTAMP
        WHERE user_id = :user_id AND id = :item_id
        """
        params = {"user_id": str(user_id), "item_id": int(item_id)}
        ok = self._execute_query(query, params, commit=True)
        if ok:
            self._insert_reading_update(user_id, item_id, "finished", value=1.0, note=None)
        return bool(ok)

    def list_reading_updates(self, user_id: int, item_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        query = """
        SELECT id, item_id, kind, value, note, created_at
        FROM reading_updates
        WHERE user_id = :user_id AND item_id = :item_id
        ORDER BY id DESC
        LIMIT :limit
        """
        params = {"user_id": str(user_id), "item_id": int(item_id), "limit": int(limit)}
        rows = self._execute_query(query, params, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def import_reading_item(
        self,
        user_id: int,
        title: str,
        *,
        author: Optional[str] = None,
        format: Optional[str] = None,
        status: str = "reading",
        total_pages: Optional[int] = None,
        total_audio_seconds: Optional[int] = None,
        current_page: Optional[int] = None,
        current_audio_seconds: Optional[int] = None,
        started_at_iso: Optional[str] = None,
        finished_at_iso: Optional[str] = None,
    ) -> Optional[int]:
        """
        Import helper that creates a reading item with optional status/timestamps.

        Important: does NOT create pages/audio delta logs (so imports don't inflate stats).
        """
        item_id = self.create_reading_item(
            user_id,
            title,
            author=author,
            format=format,
            total_pages=total_pages,
            total_audio_seconds=total_audio_seconds,
        )
        if not item_id:
            return None

        status_norm = (status or "reading").strip().lower()
        if status_norm not in ("reading", "paused", "finished", "abandoned"):
            status_norm = "reading"

        # Apply progress fields if provided (no logging)
        set_parts: List[str] = []
        params: Dict[str, Any] = {"user_id": str(user_id), "item_id": int(item_id)}
        if current_page is not None:
            try:
                set_parts.append("current_page = :current_page")
                params["current_page"] = max(0, int(current_page))
            except (TypeError, ValueError):
                pass
        if current_audio_seconds is not None:
            try:
                set_parts.append("current_audio_seconds = :current_audio_seconds")
                params["current_audio_seconds"] = max(0, int(current_audio_seconds))
            except (TypeError, ValueError):
                pass

        if started_at_iso and isinstance(started_at_iso, str) and started_at_iso.strip():
            set_parts.append("started_at = :started_at")
            params["started_at"] = started_at_iso.strip()

        # Status/finished timestamp
        set_parts.append("status = :status")
        params["status"] = status_norm

        if status_norm == "finished":
            if finished_at_iso and isinstance(finished_at_iso, str) and finished_at_iso.strip():
                set_parts.append("finished_at = :finished_at")
                params["finished_at"] = finished_at_iso.strip()
            else:
                set_parts.append("finished_at = CURRENT_TIMESTAMP")
            set_parts.append("last_update_at = COALESCE(finished_at, CURRENT_TIMESTAMP)")
        else:
            set_parts.append("last_update_at = CURRENT_TIMESTAMP")

        query = f"""
        UPDATE reading_items
        SET {", ".join(set_parts)}
        WHERE user_id = :user_id AND id = :item_id
        """
        ok = self._execute_query(query, params, commit=True)
        if not ok:
            return item_id

        if status_norm == "finished":
            # Log a finished marker for history (but still no deltas)
            self._insert_reading_update(user_id, item_id, "finished", value=1.0, note="import")

        return item_id

    def list_reading_items_all(self, user_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Lists all reading items for a user (all statuses).
        """
        query = """
        SELECT *
        FROM reading_items
        WHERE user_id = :user_id
        ORDER BY COALESCE(last_update_at, created_at) DESC, id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "limit": int(limit)}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def list_reading_updates_all(self, user_id: int, limit: int = 5000) -> List[Dict[str, Any]]:
        """
        Lists recent reading updates for a user across all items.
        """
        query = """
        SELECT id, item_id, kind, value, note, created_at
        FROM reading_updates
        WHERE user_id = :user_id
        ORDER BY id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "limit": int(limit)}, fetch_all=True)
        return rows if isinstance(rows, list) else []

    def get_reading_day_totals(self, user_id: int, day_iso: str) -> Dict[str, int]:
        """
        Returns totals for a specific UTC day (YYYY-MM-DD) based on delta logs.
        """
        query = """
        SELECT
            COALESCE(SUM(CASE WHEN kind = 'pages_delta' THEN value END), 0) AS pages,
            COALESCE(SUM(CASE WHEN kind = 'audio_delta_seconds' THEN value END), 0) AS audio_seconds
        FROM reading_updates
        WHERE user_id = :user_id AND date(created_at) = :day
        """
        row = self._execute_query(query, {"user_id": str(user_id), "day": day_iso}, fetch_one=True) or {}
        try:
            pages = int(float(row.get("pages") or 0))
        except (TypeError, ValueError):
            pages = 0
        try:
            audio_seconds = int(float(row.get("audio_seconds") or 0))
        except (TypeError, ValueError):
            audio_seconds = 0
        return {"pages": max(0, pages), "audio_seconds": max(0, audio_seconds)}

    def get_reading_daily_totals(self, user_id: int, days: int = 7) -> List[Dict[str, Any]]:
        """
        Returns per-day totals for the last N days (UTC), inclusive of today.
        Output rows: {day, pages, audio_seconds}
        """
        days = max(1, min(365, int(days)))
        query = """
        WITH RECURSIVE days(day) AS (
            SELECT date('now', 'utc', '-' || (:days - 1) || ' day')
            UNION ALL
            SELECT date(day, '+1 day') FROM days WHERE day < date('now', 'utc')
        )
        SELECT
            d.day AS day,
            COALESCE(SUM(CASE WHEN u.kind = 'pages_delta' THEN u.value END), 0) AS pages,
            COALESCE(SUM(CASE WHEN u.kind = 'audio_delta_seconds' THEN u.value END), 0) AS audio_seconds
        FROM days d
        LEFT JOIN reading_updates u
               ON date(u.created_at) = d.day
              AND u.user_id = :user_id
        GROUP BY d.day
        ORDER BY d.day ASC
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "days": days}, fetch_all=True)
        out: List[Dict[str, Any]] = []
        for r in (rows or []):
            if not isinstance(r, dict):
                continue
            day = r.get("day")
            try:
                pages = int(float(r.get("pages") or 0))
            except (TypeError, ValueError):
                pages = 0
            try:
                audio_seconds = int(float(r.get("audio_seconds") or 0))
            except (TypeError, ValueError):
                audio_seconds = 0
            out.append({"day": day, "pages": max(0, pages), "audio_seconds": max(0, audio_seconds)})
        return out

    def get_reading_range_totals(self, user_id: int, start_day_iso: str, end_day_iso: str) -> Dict[str, int]:
        """
        Returns totals for an inclusive day range (UTC) based on delta logs.
        """
        query = """
        SELECT
            COALESCE(SUM(CASE WHEN kind = 'pages_delta' THEN value END), 0) AS pages,
            COALESCE(SUM(CASE WHEN kind = 'audio_delta_seconds' THEN value END), 0) AS audio_seconds
        FROM reading_updates
        WHERE user_id = :user_id
          AND date(created_at) >= :start_day
          AND date(created_at) <= :end_day
        """
        row = self._execute_query(
            query,
            {"user_id": str(user_id), "start_day": start_day_iso, "end_day": end_day_iso},
            fetch_one=True,
        ) or {}
        try:
            pages = int(float(row.get("pages") or 0))
        except (TypeError, ValueError):
            pages = 0
        try:
            audio_seconds = int(float(row.get("audio_seconds") or 0))
        except (TypeError, ValueError):
            audio_seconds = 0
        return {"pages": max(0, pages), "audio_seconds": max(0, audio_seconds)}

    # --- Games Tracking ---
    def create_game_item(
        self,
        user_id: int,
        title: str,
        *,
        status: str = "backlog",
        platform: Optional[str] = None,
        steam_appid: Optional[int] = None,
        steam_url: Optional[str] = None,
        cover_url: Optional[str] = None,
        release_date: Optional[str] = None,
        genres: Optional[list] = None,
        developer: Optional[str] = None,
        publisher: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[int]:
        """
        Creates a new game item and returns its ID.
        """
        if not title or not str(title).strip():
            return None

        status_norm = (status or "backlog").strip().lower()
        if status_norm not in ("backlog", "playing", "paused", "completed", "dropped"):
            status_norm = "backlog"

        genres_json = None
        if genres is not None:
            try:
                genres_json = json.dumps(genres)
            except Exception:
                genres_json = None

        query = """
        INSERT INTO game_items
            (user_id, title, status, platform, steam_appid, steam_url, cover_url, release_date, genres, developer, publisher, notes, last_update_at, started_at, finished_at)
        VALUES
            (:user_id, :title, :status, :platform, :steam_appid, :steam_url, :cover_url, :release_date, :genres, :developer, :publisher, :notes,
             CURRENT_TIMESTAMP,
             CASE WHEN :status = 'playing' THEN CURRENT_TIMESTAMP ELSE NULL END,
             CASE WHEN :status = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END)
        """
        params = {
            "user_id": str(user_id),
            "title": str(title).strip(),
            "status": status_norm,
            "platform": platform.strip() if isinstance(platform, str) and platform.strip() else None,
            "steam_appid": int(steam_appid) if steam_appid is not None else None,
            "steam_url": steam_url.strip() if isinstance(steam_url, str) and steam_url.strip() else None,
            "cover_url": cover_url.strip() if isinstance(cover_url, str) and cover_url.strip() else None,
            "release_date": release_date.strip() if isinstance(release_date, str) and release_date.strip() else None,
            "genres": genres_json,
            "developer": developer.strip() if isinstance(developer, str) and developer.strip() else None,
            "publisher": publisher.strip() if isinstance(publisher, str) and publisher.strip() else None,
            "notes": notes.strip() if isinstance(notes, str) and notes.strip() else None,
        }
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, params)
                conn.commit()
                return int(cur.lastrowid)
            except sqlite3.Error as e:
                logger.error(f"create_game_item failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return None
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def get_game_item(self, user_id: int, item_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT *
        FROM game_items
        WHERE user_id = :user_id AND id = :item_id
        """
        row = self._execute_query(query, {"user_id": str(user_id), "item_id": int(item_id)}, fetch_one=True)
        if not isinstance(row, dict):
            return None
        # Decode genres JSON if present
        if row.get("genres"):
            try:
                row["genres"] = json.loads(row["genres"])
            except Exception:
                pass
        return row

    def list_game_items(
        self,
        user_id: int,
        statuses: Optional[List[str]] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        statuses = statuses or ["backlog", "playing", "paused"]
        statuses = [str(s).strip().lower() for s in statuses if isinstance(s, str) and s.strip()]
        if not statuses:
            statuses = ["backlog", "playing", "paused"]
        placeholders = ", ".join([f":s{i}" for i in range(len(statuses))])
        query = f"""
        SELECT *
        FROM game_items
        WHERE user_id = :user_id AND status IN ({placeholders})
        ORDER BY
            CASE status
                WHEN 'playing' THEN 0
                WHEN 'paused' THEN 1
                WHEN 'backlog' THEN 2
                WHEN 'completed' THEN 3
                WHEN 'dropped' THEN 4
                ELSE 5
            END,
            COALESCE(last_update_at, created_at) DESC,
            id DESC
        LIMIT :limit
        """
        params: Dict[str, Any] = {"user_id": str(user_id), "limit": int(limit)}
        for i, s in enumerate(statuses):
            params[f"s{i}"] = s
        rows = self._execute_query(query, params, fetch_all=True)
        out = rows if isinstance(rows, list) else []
        for r in out:
            if isinstance(r, dict) and r.get("genres"):
                try:
                    r["genres"] = json.loads(r["genres"])
                except Exception:
                    pass
        return out

    def list_game_items_all(self, user_id: int, limit: int = 500) -> List[Dict[str, Any]]:
        query = """
        SELECT *
        FROM game_items
        WHERE user_id = :user_id
        ORDER BY COALESCE(last_update_at, created_at) DESC, id DESC
        LIMIT :limit
        """
        rows = self._execute_query(query, {"user_id": str(user_id), "limit": int(limit)}, fetch_all=True)
        out = rows if isinstance(rows, list) else []
        for r in out:
            if isinstance(r, dict) and r.get("genres"):
                try:
                    r["genres"] = json.loads(r["genres"])
                except Exception:
                    pass
        return out

    def delete_game_item(self, user_id: int, item_id: int) -> bool:
        query = "DELETE FROM game_items WHERE user_id = :user_id AND id = :item_id"
        conn = self._get_connection()
        cur = None
        with self._lock:
            try:
                cur = conn.cursor()
                cur.execute(query, {"user_id": str(user_id), "item_id": int(item_id)})
                deleted = int(cur.rowcount or 0)
                conn.commit()
                return deleted > 0
            except sqlite3.Error as e:
                logger.error(f"delete_game_item failed: {e}")
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                return False
            finally:
                try:
                    if cur:
                        cur.close()
                except Exception:
                    pass

    def update_game_notes(self, user_id: int, item_id: int, notes: Optional[str]) -> bool:
        query = """
        UPDATE game_items
        SET notes = :notes,
            last_update_at = CURRENT_TIMESTAMP
        WHERE user_id = :user_id AND id = :item_id
        """
        params = {
            "user_id": str(user_id),
            "item_id": int(item_id),
            "notes": notes.strip() if isinstance(notes, str) and notes.strip() else None,
        }
        return bool(self._execute_query(query, params, commit=True))

    def update_game_status(self, user_id: int, item_id: int, status: str) -> bool:
        status_norm = (status or "").strip().lower()
        if status_norm not in ("backlog", "playing", "paused", "completed", "dropped"):
            return False

        # Preserve existing started_at unless moving to playing for the first time.
        # Set finished_at when completed; clear finished_at if moved away from completed.
        query = """
        UPDATE game_items
        SET status = :status,
            last_update_at = CURRENT_TIMESTAMP,
            started_at = CASE
                WHEN :status = 'playing' AND started_at IS NULL THEN CURRENT_TIMESTAMP
                ELSE started_at
            END,
            finished_at = CASE
                WHEN :status = 'completed' THEN COALESCE(finished_at, CURRENT_TIMESTAMP)
                WHEN :status != 'completed' THEN NULL
                ELSE finished_at
            END
        WHERE user_id = :user_id AND id = :item_id
        """
        params = {"user_id": str(user_id), "item_id": int(item_id), "status": status_norm}
        return bool(self._execute_query(query, params, commit=True))

    def set_current_game_item_id(self, user_id: int, item_id: Optional[int]) -> bool:
        if item_id is None:
            return self.delete_user_preference(user_id, "games_current_item_id")
        return self.set_user_preference(user_id, "games_current_item_id", int(item_id))

    def get_current_game_item_id(self, user_id: int) -> Optional[int]:
        item_id = self.get_user_preference(user_id, "games_current_item_id", None)
        if item_id is None:
            return None
        try:
            return int(item_id)
        except (TypeError, ValueError):
            return None

    def get_current_game_item(self, user_id: int) -> Optional[Dict[str, Any]]:
        preferred_id = self.get_current_game_item_id(user_id)
        if preferred_id is not None:
            item = self.get_game_item(user_id, preferred_id)
            if item:
                return item
        # fallback: latest active
        items = self.list_game_items(user_id, statuses=["playing", "paused", "backlog"], limit=1)
        return items[0] if items else None

# Example Usage (for testing)
if __name__ == "__main__":
    # Configure basic logging for standalone run
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Set dummy environment variables for testing
    os.environ['SQLITE_DB_PATH'] = 'test_bot.db' # Set SQLite env var

    try:
        logger.info("SQLite database path seems to be loaded for testing.")
        db_manager = DataManager()
        logger.info("DataManager initialized.")

        # Example: Add a TV show subscription
        user_id = 12345
        tmdb_id = 1399 # Game of Thrones
        title = "Game of Thrones"
        poster_path = "/path/to/poster.jpg"
        success = db_manager.add_tv_show_subscription(user_id, tmdb_id, title, poster_path)
        if success:
            logger.info(f"Added TV show subscription for user {user_id}, TMDB ID {tmdb_id}")
        else:
            logger.error(f"Failed to add TV show subscription for user {user_id}, TMDB ID {tmdb_id}")

    except (ValueError, ConnectionError) as e:
        logger.error(f"Application startup failed: {e}")
    finally:
        if 'db_manager' in locals() and db_manager:
            db_manager.close()
            logger.info("DataManager closed.")
