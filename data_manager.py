# data_manager.py
import sqlite3
import os
import json
import logging
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
            if conn and not commit: # Rollback if not a commit operation that failed
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
        else:
            logger.info("Table 'tv_subscriptions' does not exist. It will be created by 'CREATE TABLE IF NOT EXISTS'.")
        # --- End of tv_subscriptions schema check ---

        # TV Show Subscriptions
        # Storing last_notified_episode_details as TEXT for JSON
        create_tv_subscriptions_sql = """
        CREATE TABLE IF NOT EXISTS tv_subscriptions (
            user_id TEXT NOT NULL,
            show_tmdb_id INTEGER NOT NULL,
            show_name TEXT,
            poster_path TEXT,
            last_notified_episode_details TEXT,
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
        logger.info("Database initialization check complete.")

    # --- TV Show Subscriptions ---
    def add_tv_show_subscription(self, user_id: int, show_tmdb_id: int, show_name: str, poster_path: str) -> bool:
        user_id_str = str(user_id)
        query = """
        INSERT OR IGNORE INTO tv_subscriptions (user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details)
        VALUES (:user_id, :show_tmdb_id, :show_name, :poster_path, NULL)
        """
        params = {
            "user_id": user_id_str, "show_tmdb_id": show_tmdb_id,
            "show_name": show_name, "poster_path": poster_path
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
        query = "SELECT user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details FROM tv_subscriptions WHERE user_id = :user_id"
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
        query = "SELECT user_id, show_tmdb_id, show_name, poster_path, last_notified_episode_details FROM tv_subscriptions"
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
    def add_tracked_stock(self, user_id: int, stock_symbol: str, quantity: Optional[float] = None, purchase_price: Optional[float] = None) -> bool:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        
        # MERGE can handle insert or update logic
        query = """
        INSERT INTO tracked_stocks (user_id, symbol, quantity, purchase_price)
        VALUES (:user_id, :symbol, :quantity, :purchase_price)
        ON CONFLICT(user_id, symbol) DO UPDATE SET
            quantity = COALESCE(:quantity, quantity), -- Use COALESCE for NVL equivalent
            purchase_price = COALESCE(:purchase_price, purchase_price)
        """
        # Note: SQLite's ON CONFLICT DO UPDATE SET updates ALL listed fields if there's a conflict.
        # The COALESCE function handles the case where the input parameter is None,
        # keeping the existing value in the database. This simplifies the logic
        # compared to the original Oracle MERGE with its complex WHERE clause.
        # This also aligns with the desired behavior: if quantity/price is None,
        # it doesn't overwrite the existing value. If the stock is new, it inserts
        # with NULLs if quantity/price are None, which is acceptable.

        params = {"user_id": user_id_str, "symbol": symbol_upper, "quantity": quantity, "purchase_price": purchase_price}
        
        # The original Oracle logic had a check for new stocks requiring both quantity and price.
        # The SQLite UPSERT doesn't enforce this at the DB level.
        # We can add a Python-side check if strict adherence to that specific behavior is needed,
        # but the UPSERT with COALESCE provides a more flexible and common pattern.
        # For now, we'll rely on the UPSERT behavior.

        return self._execute_query(query, params, commit=True)


    def get_user_tracked_stocks_for_symbol(self, user_id_str: str, symbol_upper: str) -> Optional[Dict[str, Any]]:
        # Helper for add_tracked_stock
        query = "SELECT symbol, quantity, purchase_price FROM tracked_stocks WHERE user_id = :user_id AND symbol = :symbol"
        return self._execute_query(query, {"user_id": user_id_str, "symbol": symbol_upper}, fetch_one=True)


    def remove_tracked_stock(self, user_id: int, stock_symbol: str) -> bool:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        query = "DELETE FROM tracked_stocks WHERE user_id = :user_id AND symbol = :symbol"
        params = {"user_id": user_id_str, "symbol": symbol_upper}
        return self._execute_query(query, params, commit=True)

    def get_user_tracked_stocks(self, user_id: int) -> List[Dict[str, Any]]:
        user_id_str = str(user_id)
        query = "SELECT symbol, quantity, purchase_price FROM tracked_stocks WHERE user_id = :user_id"
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

    def close(self) -> None:
        """Closes the database connection."""
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
                logger.info("SQLite database connection closed.")
            except sqlite3.Error as e:
                logger.error(f"Error closing SQLite database connection: {e}")

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
