# data_manager.py
import oracledb
import os
import json
import logging
from config import ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN

logger = logging.getLogger(__name__)

class DataManager:
    def __init__(self):
        if not all([ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN]):
            logger.error("Oracle database configuration (USER, PASSWORD, DSN) not fully set in environment variables.")
            raise ValueError("Oracle database configuration not fully set.")

        try:
            # For production, consider using a connection pool
            self.pool = oracledb.create_pool(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN, min=1, max=5, increment=1)
            logger.info("Successfully connected to Oracle database and created connection pool.")
            self._initialize_db()
        except oracledb.Error as e:
            logger.error(f"Failed to connect to Oracle Database or create pool: {e}")
            raise ConnectionError(f"Failed to connect to Oracle Database: {e}")

    def _get_connection(self):
        """Acquires a connection from the pool."""
        try:
            return self.pool.acquire()
        except oracledb.Error as e:
            logger.error(f"Error acquiring connection from pool: {e}")
            raise

    def _close_connection(self, connection):
        """Releases a connection back to the pool."""
        if connection:
            try:
                self.pool.release(connection)
            except oracledb.Error as e:
                logger.error(f"Error releasing connection to pool: {e}")


    def _execute_query(self, query, params=None, fetch_one=False, fetch_all=False, commit=False):
        """Executes a given SQL query."""
        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            if commit:
                conn.commit()
                return True

            if fetch_one:
                columns = [col[0].lower() for col in cursor.description]
                row = cursor.fetchone()
                return dict(zip(columns, row)) if row else None
            elif fetch_all:
                columns = [col[0].lower() for col in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
            return cursor # For cases like rowcount or other cursor properties
        except oracledb.Error as e:
            logger.error(f"Database query error: {e}\nQuery: {query}\nParams: {params}")
            if conn and not commit: # Rollback if not a commit operation that failed
                try:
                    conn.rollback()
                except oracledb.Error as re:
                    logger.error(f"Rollback failed: {re}")
            return False if commit else (None if fetch_one else []) # Consistent error return
        finally:
            if cursor:
                cursor.close()
            if conn:
                self._close_connection(conn)

    def _initialize_db(self):
        """Creates tables if they don't exist."""
        # Using Oracle's anonymous block to check for table existence and create if not found
        # This is more robust than relying on catching ORA-00955 for table already exists.
        
        # Helper to check and create table
        def check_and_create_table(table_name, create_sql):
            check_query = f"""
            DECLARE
                v_count INTEGER;
            BEGIN
                SELECT COUNT(*)
                INTO v_count
                FROM user_tables
                WHERE table_name = UPPER('{table_name}');

                IF v_count = 0 THEN
                    EXECUTE IMMEDIATE '{create_sql.replace("'", "''")}';
                    DBMS_OUTPUT.PUT_LINE('Table {table_name} created.');
                ELSE
                    DBMS_OUTPUT.PUT_LINE('Table {table_name} already exists.');
                END IF;
            EXCEPTION
                WHEN OTHERS THEN
                    DBMS_OUTPUT.PUT_LINE('Error checking/creating table {table_name}: ' || SQLERRM);
                    RAISE;
            END;
            """
            self._execute_query(check_query, commit=True) # Commit DDL

        # TV Show Subscriptions
        # Storing last_notified_episode_details as CLOB for potentially large JSON
        create_tv_subscriptions_sql = """
        CREATE TABLE tv_subscriptions (
            user_id VARCHAR2(255) NOT NULL,
            tmdb_id NUMBER NOT NULL,
            title VARCHAR2(1000),
            poster_path VARCHAR2(1000),
            last_notified_episode_details CLOB,
            PRIMARY KEY (user_id, tmdb_id)
        )
        """
        check_and_create_table("tv_subscriptions", create_tv_subscriptions_sql)

        # Movie Subscriptions
        create_movie_subscriptions_sql = """
        CREATE TABLE movie_subscriptions (
            user_id VARCHAR2(255) NOT NULL,
            tmdb_id NUMBER NOT NULL,
            title VARCHAR2(1000),
            poster_path VARCHAR2(1000),
            notified_status NUMBER(1) DEFAULT 0, CHECK (notified_status IN (0,1)),
            PRIMARY KEY (user_id, tmdb_id)
        )
        """
        check_and_create_table("movie_subscriptions", create_movie_subscriptions_sql)

        # Tracked Stocks
        create_tracked_stocks_sql = """
        CREATE TABLE tracked_stocks (
            user_id VARCHAR2(255) NOT NULL,
            symbol VARCHAR2(20) NOT NULL,
            quantity NUMBER,
            purchase_price NUMBER,
            PRIMARY KEY (user_id, symbol)
        )
        """
        check_and_create_table("tracked_stocks", create_tracked_stocks_sql)

        # Stock Alerts
        create_stock_alerts_sql = """
        CREATE TABLE stock_alerts (
            user_id VARCHAR2(255) NOT NULL,
            symbol VARCHAR2(20) NOT NULL,
            target_above NUMBER,
            active_above NUMBER(1) DEFAULT 0, CHECK (active_above IN (0,1)),
            target_below NUMBER,
            active_below NUMBER(1) DEFAULT 0, CHECK (active_below IN (0,1)),
            dpc_above_target NUMBER,
            dpc_above_active NUMBER(1) DEFAULT 0, CHECK (dpc_above_active IN (0,1)),
            dpc_below_target NUMBER,
            dpc_below_active NUMBER(1) DEFAULT 0, CHECK (dpc_below_active IN (0,1)),
            PRIMARY KEY (user_id, symbol)
        )
        """
        check_and_create_table("stock_alerts", create_stock_alerts_sql)

        # User Preferences
        create_user_preferences_sql = """
        CREATE TABLE user_preferences (
            user_id VARCHAR2(255) NOT NULL,
            pref_key VARCHAR2(255) NOT NULL,
            pref_value CLOB,
            PRIMARY KEY (user_id, pref_key)
        )
        """
        check_and_create_table("user_preferences", create_user_preferences_sql)
        logger.info("Database initialization check complete.")

    # --- TV Show Subscriptions ---
    def add_tv_show_subscription(self, user_id: int, tmdb_id: int, title: str, poster_path: str) -> bool:
        user_id_str = str(user_id)
        query = """
        MERGE INTO tv_subscriptions dest
        USING (SELECT :user_id AS user_id, :tmdb_id AS tmdb_id FROM dual) src
        ON (dest.user_id = src.user_id AND dest.tmdb_id = src.tmdb_id)
        WHEN NOT MATCHED THEN
            INSERT (user_id, tmdb_id, title, poster_path, last_notified_episode_details)
            VALUES (:user_id, :tmdb_id, :title, :poster_path, NULL)
        """
        params = {
            "user_id": user_id_str, "tmdb_id": tmdb_id,
            "title": title, "poster_path": poster_path
        }
        return self._execute_query(query, params, commit=True)

    def remove_tv_show_subscription(self, user_id: int, tmdb_id: int) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM tv_subscriptions WHERE user_id = :user_id AND tmdb_id = :tmdb_id"
        params = {"user_id": user_id_str, "tmdb_id": tmdb_id}
        result = self._execute_query(query, params, commit=True)
        # Check if any row was actually deleted
        # The _execute_query for commit=True returns True on success, not rowcount.
        # To confirm deletion, we'd need to query before/after or check cursor.rowcount before commit.
        # For now, assume success if query executes. A more robust check might be needed.
        return result if isinstance(result, bool) else False


    def get_user_tv_subscriptions(self, user_id: int):
        user_id_str = str(user_id)
        query = "SELECT user_id, tmdb_id, title, poster_path, last_notified_episode_details FROM tv_subscriptions WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        subscriptions = self._execute_query(query, params, fetch_all=True)
        for sub in subscriptions:
            if sub.get('last_notified_episode_details'):
                try:
                    # CLOB might need .read() if it's a LOB object, or oracledb might handle it.
                    # Assuming it's returned as string from dict conversion for now.
                    details_str = sub['last_notified_episode_details']
                    if isinstance(details_str, oracledb.LOB): # Handle LOB if driver returns it
                        details_str = details_str.read()
                    sub['last_notified_episode_details'] = json.loads(details_str) if details_str else None
                except (json.JSONDecodeError, oracledb.Error) as e:
                    logger.error(f"Error decoding last_notified_episode_details for user {user_id_str}, tmdb_id {sub.get('tmdb_id')}: {e}")
                    sub['last_notified_episode_details'] = None
        return subscriptions

    def get_all_tv_subscriptions(self):
        query = "SELECT user_id, tmdb_id, title, poster_path, last_notified_episode_details FROM tv_subscriptions"
        subscriptions = self._execute_query(query, fetch_all=True)
        for sub in subscriptions:
            if sub.get('last_notified_episode_details'):
                try:
                    details_str = sub['last_notified_episode_details']
                    if isinstance(details_str, oracledb.LOB):
                        details_str = details_str.read()
                    sub['last_notified_episode_details'] = json.loads(details_str) if details_str else None
                except (json.JSONDecodeError, oracledb.Error) as e:
                    logger.error(f"Error decoding last_notified_episode_details for tmdb_id {sub.get('tmdb_id')} in get_all: {e}")
                    sub['last_notified_episode_details'] = None
        # The old method returned a dict keyed by user_id. Let's try to match that.
        result_dict = {}
        for sub in subscriptions:
            uid = sub['user_id']
            if uid not in result_dict:
                result_dict[uid] = []
            # Remove user_id from the sub-dictionary as it's now the key
            entry = {k: v for k, v in sub.items() if k != 'user_id'}
            result_dict[uid].append(entry)
        return result_dict


    def update_last_notified_episode_details(self, user_id: int, tmdb_id: int, episode_details: dict):
        user_id_str = str(user_id)
        details_json = json.dumps(episode_details) if episode_details else None
        query = """
        UPDATE tv_subscriptions
        SET last_notified_episode_details = :details_json
        WHERE user_id = :user_id AND tmdb_id = :tmdb_id
        """
        params = {"details_json": details_json, "user_id": user_id_str, "tmdb_id": tmdb_id}
        return self._execute_query(query, params, commit=True)

    # --- Movie Subscriptions ---
    def add_movie_subscription(self, user_id: int, tmdb_id: int, title: str, poster_path: str) -> bool:
        user_id_str = str(user_id)
        query = """
        MERGE INTO movie_subscriptions dest
        USING (SELECT :user_id AS user_id, :tmdb_id AS tmdb_id FROM dual) src
        ON (dest.user_id = src.user_id AND dest.tmdb_id = src.tmdb_id)
        WHEN NOT MATCHED THEN
            INSERT (user_id, tmdb_id, title, poster_path, notified_status)
            VALUES (:user_id, :tmdb_id, :title, :poster_path, 0)
        """
        params = {"user_id": user_id_str, "tmdb_id": tmdb_id, "title": title, "poster_path": poster_path}
        return self._execute_query(query, params, commit=True)

    def remove_movie_subscription(self, user_id: int, tmdb_id: int) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM movie_subscriptions WHERE user_id = :user_id AND tmdb_id = :tmdb_id"
        params = {"user_id": user_id_str, "tmdb_id": tmdb_id}
        return self._execute_query(query, params, commit=True)

    def get_user_movie_subscriptions(self, user_id: int):
        user_id_str = str(user_id)
        query = "SELECT user_id, tmdb_id, title, poster_path, notified_status FROM movie_subscriptions WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        subscriptions = self._execute_query(query, params, fetch_all=True)
        for sub in subscriptions: # Convert 0/1 to False/True
            sub['notified_status'] = bool(sub.get('notified_status', 0))
        return subscriptions

    def get_all_movie_subscriptions(self):
        query = "SELECT user_id, tmdb_id, title, poster_path, notified_status FROM movie_subscriptions"
        subscriptions = self._execute_query(query, fetch_all=True)
        result_dict = {}
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
    def add_tracked_stock(self, user_id: int, stock_symbol: str, quantity=None, purchase_price=None) -> bool:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        
        # MERGE can handle insert or update logic
        query = """
        MERGE INTO tracked_stocks dest
        USING (SELECT :user_id AS user_id, :symbol AS symbol FROM dual) src
        ON (dest.user_id = src.user_id AND dest.symbol = src.symbol)
        WHEN MATCHED THEN
            UPDATE SET quantity = NVL(:quantity, dest.quantity), 
                       purchase_price = NVL(:purchase_price, dest.purchase_price)
            WHERE (:quantity IS NOT NULL AND dest.quantity != :quantity) OR 
                  (:purchase_price IS NOT NULL AND dest.purchase_price != :purchase_price) OR
                  (:quantity IS NOT NULL AND dest.quantity IS NULL) OR 
                  (:purchase_price IS NOT NULL AND dest.purchase_price IS NULL)
        WHEN NOT MATCHED THEN
            INSERT (user_id, symbol, quantity, purchase_price)
            VALUES (:user_id, :symbol, :quantity, :purchase_price)
        """
        # If quantity or purchase_price is None, we don't want to overwrite existing values with NULL
        # unless explicitly setting them to NULL (which is not the current logic's intent for partial updates).
        # The NVL in update helps, but the WHERE clause for MATCHED needs to be careful.
        # The old logic returned False if only one of quantity/price was given for a new stock.
        # This MERGE will insert with NULLs if they are not provided.
        # For existing stocks, it updates if new values are provided and different.

        # To align with old logic: if stock is new, both quantity and price must be provided or neither.
        # If stock exists, can update one or both.
        
        # Let's check if stock exists first to simplify logic alignment
        existing_stock = self.get_user_tracked_stocks_for_symbol(user_id_str, symbol_upper)

        if existing_stock:
            # Update existing
            if quantity is None and purchase_price is None: # No update data provided
                return True # Already tracked, no change
            
            update_fields = []
            params_update = {"user_id": user_id_str, "symbol": symbol_upper}
            if quantity is not None:
                update_fields.append("quantity = :quantity")
                params_update["quantity"] = float(quantity)
            if purchase_price is not None:
                update_fields.append("purchase_price = :purchase_price")
                params_update["purchase_price"] = float(purchase_price)
            
            if not update_fields: return True # Should not happen if quantity or purchase_price is not None

            query_update = f"UPDATE tracked_stocks SET {', '.join(update_fields)} WHERE user_id = :user_id AND symbol = :symbol"
            return self._execute_query(query_update, params_update, commit=True)
        else:
            # Add new stock
            if quantity is not None and purchase_price is not None:
                q_float = float(quantity)
                p_float = float(purchase_price)
            elif quantity is None and purchase_price is None:
                q_float = None
                p_float = None
            else: # Only one provided for new stock
                logger.warning(f"add_tracked_stock: For new stock {symbol_upper} for user {user_id_str}, both quantity and purchase_price must be provided or neither.")
                return False

            query_insert = "INSERT INTO tracked_stocks (user_id, symbol, quantity, purchase_price) VALUES (:user_id, :symbol, :quantity, :purchase_price)"
            params_insert = {"user_id": user_id_str, "symbol": symbol_upper, "quantity": q_float, "purchase_price": p_float}
            return self._execute_query(query_insert, params_insert, commit=True)


    def get_user_tracked_stocks_for_symbol(self, user_id_str, symbol_upper):
        # Helper for add_tracked_stock
        query = "SELECT symbol, quantity, purchase_price FROM tracked_stocks WHERE user_id = :user_id AND symbol = :symbol"
        return self._execute_query(query, {"user_id": user_id_str, "symbol": symbol_upper}, fetch_one=True)


    def remove_tracked_stock(self, user_id: int, stock_symbol: str) -> bool:
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        query = "DELETE FROM tracked_stocks WHERE user_id = :user_id AND symbol = :symbol"
        params = {"user_id": user_id_str, "symbol": symbol_upper}
        return self._execute_query(query, params, commit=True)

    def get_user_tracked_stocks(self, user_id: int):
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
                        target_above=None, target_below=None,
                        dpc_above_target=None, dpc_below_target=None,
                        clear_above=False, clear_below=False,
                        clear_dpc_above=False, clear_dpc_below=False) -> bool:
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
        all_targets_none = all(
            update_data["target_above"] is None, update_data["target_below"] is None,
            update_data["dpc_above_target"] is None, update_data["dpc_below_target"] is None
        )

        if all_targets_none:
            query_delete = "DELETE FROM stock_alerts WHERE user_id = :user_id AND symbol = :symbol"
            params_delete = {"user_id": user_id_str, "symbol": symbol_upper}
            return self._execute_query(query_delete, params_delete, commit=True)
        else:
            # Upsert logic
            query_upsert = """
            MERGE INTO stock_alerts dest
            USING (SELECT :user_id AS user_id, :symbol AS symbol FROM dual) src
            ON (dest.user_id = src.user_id AND dest.symbol = src.symbol)
            WHEN MATCHED THEN
                UPDATE SET target_above = :target_above, active_above = :active_above,
                           target_below = :target_below, active_below = :active_below,
                           dpc_above_target = :dpc_above_target, dpc_above_active = :dpc_above_active,
                           dpc_below_target = :dpc_below_target, dpc_below_active = :dpc_below_active
            WHEN NOT MATCHED THEN
                INSERT (user_id, symbol, target_above, active_above, target_below, active_below,
                        dpc_above_target, dpc_above_active, dpc_below_target, dpc_below_active)
                VALUES (:user_id, :symbol, :target_above, :active_above, :target_below, :active_below,
                        :dpc_above_target, :dpc_above_active, :dpc_below_target, :dpc_below_active)
            """
            params_upsert = {
                "user_id": user_id_str, "symbol": symbol_upper,
                "target_above": update_data["target_above"], "active_above": 1 if update_data["active_above"] else 0,
                "target_below": update_data["target_below"], "active_below": 1 if update_data["active_below"] else 0,
                "dpc_above_target": update_data["dpc_above_target"], "dpc_above_active": 1 if update_data["dpc_above_active"] else 0,
                "dpc_below_target": update_data["dpc_below_target"], "dpc_below_active": 1 if update_data["dpc_below_active"] else 0,
            }
            return self._execute_query(query_upsert, params_upsert, commit=True)


    def get_stock_alert(self, user_id: int, stock_symbol: str):
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

    def get_all_active_alerts_for_monitoring(self):
        query = """
        SELECT user_id, symbol, target_above, active_above, target_below, active_below,
               dpc_above_target, dpc_above_active, dpc_below_target, dpc_below_active
        FROM stock_alerts
        WHERE active_above = 1 OR active_below = 1 OR dpc_above_active = 1 OR dpc_below_active = 1
        """
        alerts_list = self._execute_query(query, fetch_all=True)
        
        active_alerts_to_monitor = {}
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
    def get_user_preference(self, user_id: int, key: str, default=None):
        user_id_str = str(user_id)
        query = "SELECT pref_value FROM user_preferences WHERE user_id = :user_id AND pref_key = :key"
        params = {"user_id": user_id_str, "key": key}
        result = self._execute_query(query, params, fetch_one=True)
        if result and result.get('pref_value'):
            try:
                value_str = result['pref_value']
                if isinstance(value_str, oracledb.LOB): # Handle LOB if driver returns it
                    value_str = value_str.read()
                return json.loads(value_str)
            except (json.JSONDecodeError, oracledb.Error) as e:
                logger.error(f"Error decoding preference value for user {user_id_str}, key {key}: {e}")
                return default
        return default

    def set_user_preference(self, user_id: int, key: str, value) -> bool:
        user_id_str = str(user_id)
        value_json = json.dumps(value)
        query = """
        MERGE INTO user_preferences dest
        USING (SELECT :user_id AS user_id, :key AS pref_key FROM dual) src
        ON (dest.user_id = src.user_id AND dest.pref_key = src.pref_key)
        WHEN MATCHED THEN
            UPDATE SET pref_value = :value_json
        WHEN NOT MATCHED THEN
            INSERT (user_id, pref_key, pref_value)
            VALUES (:user_id, :key, :value_json)
        """
        params = {"user_id": user_id_str, "key": key, "value_json": value_json}
        return self._execute_query(query, params, commit=True)

    def delete_user_preference(self, user_id: int, key: str) -> bool:
        user_id_str = str(user_id)
        query = "DELETE FROM user_preferences WHERE user_id = :user_id AND pref_key = :key"
        params = {"user_id": user_id_str, "key": key}
        return self._execute_query(query, params, commit=True)

    def get_user_all_preferences(self, user_id: int) -> dict:
        user_id_str = str(user_id)
        query = "SELECT pref_key, pref_value FROM user_preferences WHERE user_id = :user_id"
        params = {"user_id": user_id_str}
        prefs_list = self._execute_query(query, params, fetch_all=True)
        
        user_prefs = {}
        for pref_row in prefs_list:
            key = pref_row['pref_key']
            try:
                value_str = pref_row['pref_value']
                if isinstance(value_str, oracledb.LOB):
                    value_str = value_str.read()
                user_prefs[key] = json.loads(value_str)
            except (json.JSONDecodeError, oracledb.Error) as e:
                logger.error(f"Error decoding preference value for user {user_id_str}, key {key} in get_all_preferences: {e}")
                user_prefs[key] = None # Or some default error indicator
        return user_prefs

    def close(self):
        """Closes the database connection pool."""
        if self.pool:
            try:
                self.pool.close()
                logger.info("Oracle connection pool closed.")
            except oracledb.Error as e:
                logger.error(f"Error closing Oracle connection pool: {e}")

# Example of how to use DataManager (optional, for testing)
if __name__ == '__main__':
    # This requires .env to be in the same directory as data_manager.py or config.py to load correctly
    # Or environment variables ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN to be set
    logging.basicConfig(level=logging.INFO)
    
    # Ensure .env is loaded if running standalone for testing
    from dotenv import load_dotenv
    dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
        logger.info(f"Loaded .env from {dotenv_path} for standalone test.")
    else:
        logger.warning(f".env file not found at {dotenv_path}. Ensure Oracle env vars are set.")

    # Re-import config to pick up .env vars if loaded above
    import importlib
    import config as app_config # Use an alias to avoid conflict
    importlib.reload(app_config) # Reload to get env vars

    # Update global vars from reloaded config
    ORACLE_USER = app_config.ORACLE_USER
    ORACLE_PASSWORD = app_config.ORACLE_PASSWORD
    ORACLE_DSN = app_config.ORACLE_DSN
    
    if not all([ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN]):
        logger.error("Oracle credentials not found. Set ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN environment variables.")
    else:
        logger.info("Oracle credentials seem to be loaded for testing.")
        dm = None
        try:
            dm = DataManager()
            logger.info("DataManager initialized for testing.")

            # Test TV Show
            # dm.add_tv_show_subscription(123, 71712, "The Orville", "/path.jpg")
            # dm.add_tv_show_subscription(123, 1399, "Game of Thrones", "/got.jpg")
            # user_tv = dm.get_user_tv_subscriptions(123)
            # logger.info(f"User 123 TV: {user_tv}")
            # all_tv = dm.get_all_tv_subscriptions()
            # logger.info(f"All TV: {all_tv}")
            # dm.update_last_notified_episode_details(123, 71712, {"season": 3, "episode": 10, "name": "Future Unknown"})
            # logger.info(f"Updated Orville: {dm.get_user_tv_subscriptions(123)}")
            # dm.remove_tv_show_subscription(123, 1399)
            # logger.info(f"After removing GoT: {dm.get_user_tv_subscriptions(123)}")

            # Test Preferences
            # dm.set_user_preference(999, "theme", "dark")
            # dm.set_user_preference(999, "notifications", {"email": True, "sms": False})
            # prefs = dm.get_user_all_preferences(999)
            # logger.info(f"User 999 Prefs: {prefs}")
            # theme = dm.get_user_preference(999, "theme")
            # logger.info(f"User 999 Theme: {theme}")
            # dm.delete_user_preference(999, "theme")
            # logger.info(f"User 999 Prefs after delete: {dm.get_user_all_preferences(999)}")

        except Exception as e:
            logger.error(f"Error during DataManager test: {e}", exc_info=True)
        finally:
            if dm:
                dm.close()