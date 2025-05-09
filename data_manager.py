# data_manager.py
import json
import os
from azure.storage.blob import BlobServiceClient, BlobClient # BlobClient imported as per request, though get_blob_client is used
from azure.core.exceptions import ResourceNotFoundError
# Consider adding proper logging instead of print statements for production
# import logging
# logger = logging.getLogger(__name__)

class DataManager:
    def __init__(self):
        self.connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        self.container_name = os.environ.get("AZURE_STORAGE_CONTAINER_NAME")

        if not self.connection_string:
            # logger.error("AZURE_STORAGE_CONNECTION_STRING environment variable not set.")
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING environment variable not set.")
        if not self.container_name:
            # logger.error("AZURE_STORAGE_CONTAINER_NAME environment variable not set.")
            raise ValueError("AZURE_STORAGE_CONTAINER_NAME environment variable not set.")

        try:
            self.blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
        except Exception as e:
            # logger.error(f"Failed to connect to Azure Blob Storage: {e}")
            raise ConnectionError(f"Failed to connect to Azure Blob Storage: {e}")

    def _load_json(self, blob_name: str) -> dict:
        """Loads data from a JSON blob. Returns an empty dict if blob doesn't exist or is invalid."""
        try:
            blob_client = self.blob_service_client.get_blob_client(container=self.container_name, blob=blob_name)
            if not blob_client.exists():
                # print(f"DataManager: Blob '{blob_name}' does not exist in container '{self.container_name}'. Returning empty dict.")
                return {}
            
            downloader = blob_client.download_blob(timeout=30) # Adding a timeout
            blob_content_bytes = downloader.readall()
            
            if not blob_content_bytes: # Handle completely empty blob
                # print(f"DataManager: Blob '{blob_name}' is empty. Returning empty dict.")
                return {}

            blob_content_str = blob_content_bytes.decode('utf-8')
            
            if not blob_content_str.strip(): # Handle blob with only whitespace
                # print(f"DataManager: Blob '{blob_name}' contains only whitespace. Returning empty dict.")
                return {}
                
            return json.loads(blob_content_str)
        except ResourceNotFoundError:
            # This case should ideally be caught by blob_client.exists(), but serves as a fallback.
            # print(f"DataManager: Blob '{blob_name}' not found (ResourceNotFoundError). Returning empty dict.")
            return {}
        except json.JSONDecodeError:
            # print(f"DataManager: Failed to decode JSON from blob '{blob_name}'. Returning empty dict.")
            return {} 
        except Exception as e: 
            print(f"DataManager: Error loading blob '{blob_name}' from container '{self.container_name}': {e}")
            return {}

    def _save_json(self, blob_name: str, data: dict) -> bool:
        """Saves data to a JSON blob."""
        try:
            blob_client = self.blob_service_client.get_blob_client(container=self.container_name, blob=blob_name)
            json_data_str = json.dumps(data, indent=4)
            blob_client.upload_blob(json_data_str.encode('utf-8'), overwrite=True, timeout=30) # Encode to bytes
            return True
        except Exception as e:
            print(f"DataManager: Error saving blob '{blob_name}' to container '{self.container_name}': {e}")
            return False

    # --- TV Show Subscriptions ---
    TV_SUBSCRIPTIONS_BLOB = "tv_subscriptions.json"
    MOVIE_SUBSCRIPTIONS_BLOB = "movie_subscriptions.json"
    TRACKED_STOCKS_BLOB = "tracked_stocks.json"
    STOCK_ALERTS_BLOB = "stock_alerts.json"
    USER_PREFERENCES_BLOB = "user_preferences.json"
    
    def add_tv_subscription(self, user_id, show_id, show_name):
        """Adds a TV show subscription for a user."""
        user_id_str = str(user_id) 
        subscriptions = self._load_json(self.TV_SUBSCRIPTIONS_BLOB)
        
        if user_id_str not in subscriptions:
            subscriptions[user_id_str] = []
        
        if not any(sub['show_id'] == show_id for sub in subscriptions[user_id_str]):
            subscriptions[user_id_str].append({
                "show_id": show_id,
                "show_name": show_name,
                "last_notified_episode_details": None 
            })
            return self._save_json(self.TV_SUBSCRIPTIONS_BLOB, subscriptions)
        return False 

    def remove_tv_subscription(self, user_id, show_id):
        """Removes a TV show subscription for a user."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.TV_SUBSCRIPTIONS_BLOB)
        
        if user_id_str in subscriptions:
            original_len = len(subscriptions[user_id_str])
            subscriptions[user_id_str] = [sub for sub in subscriptions[user_id_str] if sub['show_id'] != show_id]
            if len(subscriptions[user_id_str]) < original_len: 
                 if not subscriptions[user_id_str]: 
                    del subscriptions[user_id_str]
                 return self._save_json(self.TV_SUBSCRIPTIONS_BLOB, subscriptions)
        return False

    def get_user_tv_subscriptions(self, user_id):
        """Gets all TV show subscriptions for a specific user."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.TV_SUBSCRIPTIONS_BLOB)
        return subscriptions.get(user_id_str, [])

    def get_all_tv_subscriptions(self):
        """Gets all TV show subscriptions for all users."""
        return self._load_json(self.TV_SUBSCRIPTIONS_BLOB)

    def update_last_notified_episode_details(self, user_id, show_id, episode_details):
        """Updates the last notified episode details for a user's specific show subscription."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.TV_SUBSCRIPTIONS_BLOB)
        updated = False
        if user_id_str in subscriptions:
            for sub in subscriptions[user_id_str]:
                if sub['show_id'] == show_id:
                    sub['last_notified_episode_details'] = episode_details
                    updated = True
                    break
        if updated:
            return self._save_json(self.TV_SUBSCRIPTIONS_BLOB, subscriptions)
        return False

    def add_tv_show_subscription(self, user_id: int, tmdb_id: int, title: str, poster_path: str) -> bool:
        """Adds a TV show subscription for a user using TMDB ID, title, and poster path."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.TV_SUBSCRIPTIONS_BLOB)

        if user_id_str not in subscriptions:
            subscriptions[user_id_str] = []

        if any(sub.get('tmdb_id') == tmdb_id for sub in subscriptions[user_id_str]):
            return True 

        subscriptions[user_id_str].append({
            "tmdb_id": tmdb_id,
            "title": title,
            "poster_path": poster_path,
            "last_notified_episode_details": None
        })
        
        return self._save_json(self.TV_SUBSCRIPTIONS_BLOB, subscriptions)

    def remove_tv_show_subscription(self, user_id: int, tmdb_id: int) -> bool:
        """Removes a TV show subscription for a user based on TMDB ID."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.TV_SUBSCRIPTIONS_BLOB)
        
        if user_id_str not in subscriptions or not subscriptions[user_id_str]:
            return False 

        user_subs = subscriptions[user_id_str]
        original_len = len(user_subs)
        
        filtered_subs = [
            sub for sub in user_subs 
            if not (isinstance(sub, dict) and sub.get('tmdb_id') == tmdb_id)
        ]
        
        if len(filtered_subs) < original_len: 
            if not filtered_subs: 
                del subscriptions[user_id_str] 
            else:
                subscriptions[user_id_str] = filtered_subs
            
            return self._save_json(self.TV_SUBSCRIPTIONS_BLOB, subscriptions)
        else:
            return False

    # --- Movie Subscriptions ---
    def add_movie_subscription(self, user_id: int, tmdb_id: int, title: str, poster_path: str) -> bool:
        """Adds a movie subscription for a user using TMDB ID, title, and poster path."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.MOVIE_SUBSCRIPTIONS_BLOB)

        if user_id_str not in subscriptions:
            subscriptions[user_id_str] = []

        if any(sub.get('tmdb_id') == tmdb_id for sub in subscriptions[user_id_str]):
            return True

        subscriptions[user_id_str].append({
            "tmdb_id": tmdb_id,
            "title": title,
            "poster_path": poster_path,
            "notified_status": False
        })
        
        return self._save_json(self.MOVIE_SUBSCRIPTIONS_BLOB, subscriptions)

    def remove_movie_subscription(self, user_id, tmdb_id):
        """Removes a movie subscription for a user."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.MOVIE_SUBSCRIPTIONS_BLOB)
        
        if user_id_str in subscriptions:
            original_len = len(subscriptions[user_id_str])
            subscriptions[user_id_str] = [sub for sub in subscriptions[user_id_str] if sub.get('tmdb_id') != tmdb_id]
            if len(subscriptions[user_id_str]) < original_len:
                 if not subscriptions[user_id_str]:
                    del subscriptions[user_id_str]
                 return self._save_json(self.MOVIE_SUBSCRIPTIONS_BLOB, subscriptions)
        return False

    def get_user_movie_subscriptions(self, user_id):
        """Gets all movie subscriptions for a specific user."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.MOVIE_SUBSCRIPTIONS_BLOB)
        return subscriptions.get(user_id_str, [])

    def get_all_movie_subscriptions(self):
        """Gets all movie subscriptions for all users."""
        return self._load_json(self.MOVIE_SUBSCRIPTIONS_BLOB)

    def update_movie_notified_status(self, user_id: int, tmdb_id: int, status: bool) -> bool:
        """Updates the notified status for a user's specific movie subscription."""
        user_id_str = str(user_id)
        subscriptions = self._load_json(self.MOVIE_SUBSCRIPTIONS_BLOB)
        updated = False
        if user_id_str in subscriptions:
            for sub in subscriptions[user_id_str]:
                if sub.get('tmdb_id') == tmdb_id:
                    sub['notified_status'] = status
                    updated = True
                    break
        if updated:
            return self._save_json(self.MOVIE_SUBSCRIPTIONS_BLOB, subscriptions)
        return False

    # --- Tracked Stocks ---
    def add_tracked_stock(self, user_id, stock_symbol, quantity=None, purchase_price=None):
        """Adds or updates a stock in a user's tracked list."""
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        stocks_data = self._load_json(self.TRACKED_STOCKS_BLOB)

        if user_id_str not in stocks_data:
            stocks_data[user_id_str] = []

        user_stocks = stocks_data[user_id_str]
        stock_entry = None
        # entry_index = -1 # Not strictly needed with current logic but good for clarity if modifying list in place
        
        # Find existing stock entry
        found_stock_to_update = False
        for i, s in enumerate(user_stocks):
            if isinstance(s, dict) and s.get("symbol") == symbol_upper:
                stock_entry = s
                # entry_index = i
                found_stock_to_update = True
                break
            elif isinstance(s, str) and s == symbol_upper: # Handle old format
                stock_entry = {"symbol": symbol_upper}
                user_stocks[i] = stock_entry # Convert to new format
                # entry_index = i
                found_stock_to_update = True
                break
        
        if found_stock_to_update and stock_entry is not None: # Stock already tracked
            updated_portfolio_data = False
            if quantity is not None and purchase_price is not None:
                try:
                    new_quantity = float(quantity)
                    new_purchase_price = float(purchase_price)
                    
                    # Update if different or if keys were missing
                    if stock_entry.get("quantity") != new_quantity or \
                       stock_entry.get("purchase_price") != new_purchase_price:
                        stock_entry["quantity"] = new_quantity
                        stock_entry["purchase_price"] = new_purchase_price
                        updated_portfolio_data = True
                except ValueError:
                    return False # Invalid quantity or price format
            elif quantity is not None or purchase_price is not None:
                 # If only one is provided, it's an incomplete update
                return False 

            if updated_portfolio_data:
                return self._save_json(self.TRACKED_STOCKS_BLOB, stocks_data)
            return True # Already tracked, no portfolio update needed or no new data provided
        else: # New stock to track
            new_entry = {"symbol": symbol_upper}
            if quantity is not None and purchase_price is not None:
                try:
                    new_entry["quantity"] = float(quantity)
                    new_entry["purchase_price"] = float(purchase_price)
                except ValueError:
                    return False 
            elif quantity is not None or purchase_price is not None:
                return False # Require both for initial portfolio details
            
            user_stocks.append(new_entry)
            return self._save_json(self.TRACKED_STOCKS_BLOB, stocks_data)

    def remove_tracked_stock(self, user_id, stock_symbol):
        """Removes a stock from a user's tracked list."""
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        stocks_data = self._load_json(self.TRACKED_STOCKS_BLOB)

        if user_id_str in stocks_data:
            user_stocks = stocks_data[user_id_str]
            original_len = len(user_stocks)
            
            new_user_stocks = []
            for s in user_stocks:
                if isinstance(s, dict) and s.get("symbol") == symbol_upper:
                    continue 
                elif isinstance(s, str) and s == symbol_upper: 
                    continue 
                new_user_stocks.append(s)

            if len(new_user_stocks) < original_len:
                if not new_user_stocks:
                    del stocks_data[user_id_str]
                else:
                    stocks_data[user_id_str] = new_user_stocks
                return self._save_json(self.TRACKED_STOCKS_BLOB, stocks_data)
        return False

    def get_user_tracked_stocks(self, user_id):
        """Gets all tracked stocks for a specific user."""
        user_id_str = str(user_id)
        stocks_data = self._load_json(self.TRACKED_STOCKS_BLOB)
        user_stocks_raw = stocks_data.get(user_id_str, [])
        
        processed_stocks = []
        for s_entry in user_stocks_raw:
            if isinstance(s_entry, str):
                processed_stocks.append({"symbol": s_entry.upper()})
            elif isinstance(s_entry, dict) and "symbol" in s_entry:
                s_entry["symbol"] = s_entry["symbol"].upper()
                if "quantity" in s_entry and s_entry["quantity"] is not None:
                    try:
                        s_entry["quantity"] = float(s_entry["quantity"])
                    except ValueError: 
                        s_entry.pop("quantity", None)
                if "purchase_price" in s_entry and s_entry["purchase_price"] is not None:
                    try:
                        s_entry["purchase_price"] = float(s_entry["purchase_price"])
                    except ValueError:
                        s_entry.pop("purchase_price", None)
                processed_stocks.append(s_entry)
                
        return processed_stocks

    # --- Stock Alerts ---
    def add_stock_alert(self, user_id, stock_symbol,
                        target_above=None, target_below=None,
                        dpc_above_target=None, dpc_below_target=None,
                        clear_above=False, clear_below=False,
                        clear_dpc_above=False, clear_dpc_below=False):
        """Adds or updates a stock alert for a user."""
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        alerts = self._load_json(self.STOCK_ALERTS_BLOB)

        if user_id_str not in alerts:
            alerts[user_id_str] = {}
        
        if symbol_upper not in alerts[user_id_str]:
            alerts[user_id_str][symbol_upper] = {
                "target_above": None, "active_above": False,
                "target_below": None, "active_below": False,
                "dpc_above_target": None, "dpc_above_active": False,
                "dpc_below_target": None, "dpc_below_active": False
            }

        user_stock_alerts = alerts[user_id_str][symbol_upper]
        updated = False

        # Price Above
        if clear_above:
            if user_stock_alerts.get("target_above") is not None:
                user_stock_alerts["target_above"] = None; user_stock_alerts["active_above"] = False; updated = True
        elif target_above is not None:
            try:
                price = float(target_above)
                if user_stock_alerts.get("target_above") != price or not user_stock_alerts.get("active_above"):
                    user_stock_alerts["target_above"] = price; user_stock_alerts["active_above"] = True; updated = True
            except ValueError: pass
        
        # Price Below
        if clear_below:
            if user_stock_alerts.get("target_below") is not None:
                user_stock_alerts["target_below"] = None; user_stock_alerts["active_below"] = False; updated = True
        elif target_below is not None:
            try:
                price = float(target_below)
                if user_stock_alerts.get("target_below") != price or not user_stock_alerts.get("active_below"):
                    user_stock_alerts["target_below"] = price; user_stock_alerts["active_below"] = True; updated = True
            except ValueError: pass

        # DPC Above
        if clear_dpc_above:
            if user_stock_alerts.get("dpc_above_target") is not None:
                user_stock_alerts["dpc_above_target"] = None; user_stock_alerts["dpc_above_active"] = False; updated = True
        elif dpc_above_target is not None:
            try:
                percent = float(dpc_above_target)
                if user_stock_alerts.get("dpc_above_target") != percent or not user_stock_alerts.get("dpc_above_active"):
                    user_stock_alerts["dpc_above_target"] = percent; user_stock_alerts["dpc_above_active"] = True; updated = True
            except ValueError: pass

        # DPC Below
        if clear_dpc_below:
            if user_stock_alerts.get("dpc_below_target") is not None:
                user_stock_alerts["dpc_below_target"] = None; user_stock_alerts["dpc_below_active"] = False; updated = True
        elif dpc_below_target is not None:
            try:
                percent = float(dpc_below_target)
                if user_stock_alerts.get("dpc_below_target") != percent or not user_stock_alerts.get("dpc_below_active"):
                    user_stock_alerts["dpc_below_target"] = percent; user_stock_alerts["dpc_below_active"] = True; updated = True
            except ValueError: pass
                
        if updated:
            if all(v is None for k, v in user_stock_alerts.items() if "target" in k): # Check if all targets are None
                del alerts[user_id_str][symbol_upper]
                if not alerts[user_id_str]: 
                    del alerts[user_id_str]
            return self._save_json(self.STOCK_ALERTS_BLOB, alerts)
        return False

    def get_stock_alert(self, user_id, stock_symbol):
        """Gets the alert settings for a specific stock for a user."""
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        alerts = self._load_json(self.STOCK_ALERTS_BLOB)
        return alerts.get(user_id_str, {}).get(symbol_upper, None)

    def remove_stock_alert_target(self, user_id, stock_symbol, direction):
        """Removes a specific price target (above or below) for a stock alert."""
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        alerts = self._load_json(self.STOCK_ALERTS_BLOB)
        changed = False

        if user_id_str in alerts and symbol_upper in alerts[user_id_str]:
            alert_data = alerts[user_id_str][symbol_upper]
            if direction == "above" and alert_data.get("target_above") is not None:
                alert_data["target_above"] = None; alert_data["active_above"] = False; changed = True
            elif direction == "below" and alert_data.get("target_below") is not None:
                alert_data["target_below"] = None; alert_data["active_below"] = False; changed = True
            
            if changed:
                if all(v is None for k, v in alert_data.items() if "target" in k):
                    del alerts[user_id_str][symbol_upper]
                    if not alerts[user_id_str]: del alerts[user_id_str]
                return self._save_json(self.STOCK_ALERTS_BLOB, alerts)
        return False

    def deactivate_stock_alert_target(self, user_id, stock_symbol, direction):
        """Deactivates a specific target (price or DPC) (e.g., after it has been triggered)."""
        user_id_str = str(user_id)
        symbol_upper = stock_symbol.upper()
        alerts = self._load_json(self.STOCK_ALERTS_BLOB)
        changed = False

        if user_id_str in alerts and symbol_upper in alerts[user_id_str]:
            alert_data = alerts[user_id_str][symbol_upper]
            if direction == "above" and alert_data.get("active_above"):
                alert_data["active_above"] = False; changed = True
            elif direction == "below" and alert_data.get("active_below"):
                alert_data["active_below"] = False; changed = True
            elif direction == "dpc_above" and alert_data.get("dpc_above_active"):
                alert_data["dpc_above_active"] = False; changed = True
            elif direction == "dpc_below" and alert_data.get("dpc_below_active"):
                alert_data["dpc_below_active"] = False; changed = True
            
            if changed:
                return self._save_json(self.STOCK_ALERTS_BLOB, alerts)
        return False

    def get_all_active_alerts_for_monitoring(self):
        """
        Retrieves all stock alerts for all users that have at least one active target.
        Returns a dictionary: {user_id: {stock_symbol: alert_details}}
        """
        all_alerts_data = self._load_json(self.STOCK_ALERTS_BLOB)
        active_alerts_to_monitor = {}

        for user_id_str, user_alerts in all_alerts_data.items():
            user_active_alerts = {}
            for symbol, alert_details in user_alerts.items():
                is_active = (
                    (alert_details.get("target_above") is not None and alert_details.get("active_above")) or
                    (alert_details.get("target_below") is not None and alert_details.get("active_below")) or
                    (alert_details.get("dpc_above_target") is not None and alert_details.get("dpc_above_active")) or
                    (alert_details.get("dpc_below_target") is not None and alert_details.get("dpc_below_active"))
                )
                if is_active:
                    user_active_alerts[symbol] = alert_details
            
            if user_active_alerts: # Only add user if they have active alerts
                active_alerts_to_monitor[user_id_str] = user_active_alerts
                
        return active_alerts_to_monitor

    def get_user_all_stock_alerts(self, user_id):
        """Gets all stock alert settings for a specific user."""
        user_id_str = str(user_id)
        alerts = self._load_json(self.STOCK_ALERTS_BLOB)
        return alerts.get(user_id_str, {})

    # --- User Preferences ---
    def get_user_all_preferences(self, user_id):
        """Gets all preferences for a specific user."""
        user_id_str = str(user_id)
        preferences = self._load_json(self.USER_PREFERENCES_BLOB)
        return preferences.get(user_id_str, {})

    def get_user_preference(self, user_id, key, default=None):
        """Gets a specific preference for a user."""
        user_id_str = str(user_id)
        preferences = self._load_json(self.USER_PREFERENCES_BLOB)
        return preferences.get(user_id_str, {}).get(key, default)

    def set_user_preference(self, user_id, key, value):
        """Sets a specific preference for a user."""
        user_id_str = str(user_id)
        preferences = self._load_json(self.USER_PREFERENCES_BLOB)
        if user_id_str not in preferences:
            preferences[user_id_str] = {}
        preferences[user_id_str][key] = value
        return self._save_json(self.USER_PREFERENCES_BLOB, preferences)

    def delete_user_preference(self, user_id, key) -> bool:
        """Deletes a specific preference for a user. Returns True if key was found and deleted."""
        user_id_str = str(user_id)
        preferences = self._load_json(self.USER_PREFERENCES_BLOB)
        if user_id_str in preferences and key in preferences[user_id_str]:
            del preferences[user_id_str][key]
            if not preferences[user_id_str]: # If user has no more preferences, remove user key
                del preferences[user_id_str]
            return self._save_json(self.USER_PREFERENCES_BLOB, preferences)
        return False

    def get_all_user_preferences(self):
        """Gets all preferences for all users."""
        return self._load_json(self.USER_PREFERENCES_BLOB)

# Example of how this class might be instantiated and used by other modules:
# if __name__ == '__main__':
#     # This requires AZURE_STORAGE_CONNECTION_STRING and AZURE_STORAGE_CONTAINER_NAME to be set in environment
#     try:
#         data_manager_instance = DataManager()
#         print("DataManager initialized successfully.")
#         # Example usage:
#         # test_data = {"hello": "world_azure"}
#         # save_result = data_manager_instance._save_json("test_blob.json", test_data)
#         # print(f"Save result: {save_result}")
#         # loaded_data = data_manager_instance._load_json("test_blob.json")
#         # print(f"Loaded data: {loaded_data}")
#     except (ValueError, ConnectionError) as e:
#         print(f"Failed to initialize DataManager: {e}")