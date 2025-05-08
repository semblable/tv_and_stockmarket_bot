# data_manager.py

import json
import os

DATA_DIR = "data"
TV_SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "tv_subscriptions.json")
MOVIE_SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "movie_subscriptions.json") # New file for movie subscriptions
TRACKED_STOCKS_FILE = os.path.join(DATA_DIR, "tracked_stocks.json")
STOCK_ALERTS_FILE = os.path.join(DATA_DIR, "stock_alerts.json")
USER_PREFERENCES_FILE = os.path.join(DATA_DIR, "user_preferences.json")

# Ensure data directory exists
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def _load_json(file_path):
    """Loads data from a JSON file. Returns an empty dict if file doesn't exist or is invalid."""
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def _save_json(file_path, data):
    """Saves data to a JSON file."""
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=4)
        return True
    except IOError:
        return False

# --- TV Show Subscriptions ---

def add_tv_subscription(user_id, show_id, show_name):
    """Adds a TV show subscription for a user."""
    user_id_str = str(user_id) # JSON keys must be strings
    subscriptions = _load_json(TV_SUBSCRIPTIONS_FILE)
    
    if user_id_str not in subscriptions:
        subscriptions[user_id_str] = []
    
    # Avoid duplicate subscriptions for the same show
    if not any(sub['show_id'] == show_id for sub in subscriptions[user_id_str]):
        subscriptions[user_id_str].append({
            "show_id": show_id,
            "show_name": show_name,
            "last_notified_episode_details": None # Changed from last_notified_episode_id
        })
        return _save_json(TV_SUBSCRIPTIONS_FILE, subscriptions)
    return False # Already subscribed or error

def remove_tv_subscription(user_id, show_id):
    """Removes a TV show subscription for a user."""
    user_id_str = str(user_id)
    subscriptions = _load_json(TV_SUBSCRIPTIONS_FILE)
    
    if user_id_str in subscriptions:
        original_len = len(subscriptions[user_id_str])
        subscriptions[user_id_str] = [sub for sub in subscriptions[user_id_str] if sub['show_id'] != show_id]
        if len(subscriptions[user_id_str]) < original_len: # Check if something was actually removed
             if not subscriptions[user_id_str]: # If list is empty, remove user key
                del subscriptions[user_id_str]
             return _save_json(TV_SUBSCRIPTIONS_FILE, subscriptions)
    return False # Not found or error

def get_user_tv_subscriptions(user_id):
    """Gets all TV show subscriptions for a specific user."""
    user_id_str = str(user_id)
    subscriptions = _load_json(TV_SUBSCRIPTIONS_FILE)
    return subscriptions.get(user_id_str, [])

def get_all_tv_subscriptions():
    """Gets all TV show subscriptions for all users."""
    return _load_json(TV_SUBSCRIPTIONS_FILE)

def update_last_notified_episode_details(user_id, show_id, episode_details):
    """Updates the last notified episode details for a user's specific show subscription."""
    user_id_str = str(user_id)
    subscriptions = _load_json(TV_SUBSCRIPTIONS_FILE)
    updated = False
    if user_id_str in subscriptions:
        for sub in subscriptions[user_id_str]:
            if sub['show_id'] == show_id:
                sub['last_notified_episode_details'] = episode_details # Store the whole dict
                updated = True
                break
    if updated:
        return _save_json(TV_SUBSCRIPTIONS_FILE, subscriptions)
    return False

# --- Movie Subscriptions ---

def add_movie_subscription(user_id, movie_id, movie_title, release_date):
    """Adds a movie subscription for a user."""
    user_id_str = str(user_id)
    subscriptions = _load_json(MOVIE_SUBSCRIPTIONS_FILE)
    
    if user_id_str not in subscriptions:
        subscriptions[user_id_str] = []
    
    # Avoid duplicate subscriptions for the same movie
    if not any(sub['movie_id'] == movie_id for sub in subscriptions[user_id_str]):
        subscriptions[user_id_str].append({
            "movie_id": movie_id,
            "movie_title": movie_title,
            "release_date": release_date, # Store release date
            "notified_status": False # To track if notification for release has been sent
        })
        return _save_json(MOVIE_SUBSCRIPTIONS_FILE, subscriptions)
    return False # Already subscribed or error

def remove_movie_subscription(user_id, movie_id):
    """Removes a movie subscription for a user."""
    user_id_str = str(user_id)
    subscriptions = _load_json(MOVIE_SUBSCRIPTIONS_FILE)
    
    if user_id_str in subscriptions:
        original_len = len(subscriptions[user_id_str])
        subscriptions[user_id_str] = [sub for sub in subscriptions[user_id_str] if sub['movie_id'] != movie_id]
        if len(subscriptions[user_id_str]) < original_len:
             if not subscriptions[user_id_str]:
                del subscriptions[user_id_str]
             return _save_json(MOVIE_SUBSCRIPTIONS_FILE, subscriptions)
    return False

def get_user_movie_subscriptions(user_id):
    """Gets all movie subscriptions for a specific user."""
    user_id_str = str(user_id)
    subscriptions = _load_json(MOVIE_SUBSCRIPTIONS_FILE)
    return subscriptions.get(user_id_str, [])

def get_all_movie_subscriptions():
    """Gets all movie subscriptions for all users."""
    return _load_json(MOVIE_SUBSCRIPTIONS_FILE)

def update_movie_notified_status(user_id, movie_id, status: bool):
    """Updates the notified status for a user's specific movie subscription."""
    user_id_str = str(user_id)
    subscriptions = _load_json(MOVIE_SUBSCRIPTIONS_FILE)
    updated = False
    if user_id_str in subscriptions:
        for sub in subscriptions[user_id_str]:
            if sub['movie_id'] == movie_id:
                sub['notified_status'] = status
                updated = True
                break
    if updated:
        return _save_json(MOVIE_SUBSCRIPTIONS_FILE, subscriptions)
    return False

# --- Tracked Stocks ---

def add_tracked_stock(user_id, stock_symbol, quantity=None, purchase_price=None):
    """
    Adds or updates a stock in a user's tracked list.
    Optionally includes quantity and purchase price for portfolio tracking.
    If quantity and purchase_price are provided, they update any existing entry.
    If they are not provided and the stock is already tracked with portfolio data,
    the portfolio data remains.
    """
    user_id_str = str(user_id)
    symbol_upper = stock_symbol.upper()
    stocks_data = _load_json(TRACKED_STOCKS_FILE)

    if user_id_str not in stocks_data:
        stocks_data[user_id_str] = []

    user_stocks = stocks_data[user_id_str]
    stock_entry = None
    for i, s in enumerate(user_stocks):
        if isinstance(s, dict) and s.get("symbol") == symbol_upper:
            stock_entry = s
            entry_index = i
            break
        elif isinstance(s, str) and s == symbol_upper: # Handle old format
            stock_entry = {"symbol": symbol_upper}
            entry_index = i
            user_stocks[entry_index] = stock_entry # Convert to new format
            break

    if stock_entry: # Stock already tracked, update if new data provided
        updated = False
        if quantity is not None and purchase_price is not None:
            try:
                current_quantity = stock_entry.get("quantity")
                current_purchase_price = stock_entry.get("purchase_price")
                new_quantity = float(quantity)
                new_purchase_price = float(purchase_price)

                if current_quantity != new_quantity or current_purchase_price != new_purchase_price:
                    stock_entry["quantity"] = new_quantity
                    stock_entry["purchase_price"] = new_purchase_price
                    updated = True
            except ValueError:
                return False # Invalid quantity or price format
        elif quantity is not None or purchase_price is not None:
             # If only one is provided, it's an incomplete update, treat as error or ignore
             # For now, let's require both for an update of portfolio details
            return False # Or handle as per specific requirement, e.g., log warning

        if updated:
            return _save_json(TRACKED_STOCKS_FILE, stocks_data)
        return True # Already tracked, no portfolio update needed or no new data provided
    else: # New stock to track
        new_entry = {"symbol": symbol_upper}
        if quantity is not None and purchase_price is not None:
            try:
                new_entry["quantity"] = float(quantity)
                new_entry["purchase_price"] = float(purchase_price)
            except ValueError:
                return False # Invalid quantity or price format
        elif quantity is not None or purchase_price is not None:
            # Require both for initial portfolio details
            return False

        user_stocks.append(new_entry)
        return _save_json(TRACKED_STOCKS_FILE, stocks_data)

def remove_tracked_stock(user_id, stock_symbol):
    """Removes a stock from a user's tracked list."""
    user_id_str = str(user_id)
    symbol_upper = stock_symbol.upper()
    stocks_data = _load_json(TRACKED_STOCKS_FILE)

    if user_id_str in stocks_data:
        user_stocks = stocks_data[user_id_str]
        original_len = len(user_stocks)
        
        new_user_stocks = []
        for s in user_stocks:
            if isinstance(s, dict) and s.get("symbol") == symbol_upper:
                continue # Skip this stock
            elif isinstance(s, str) and s == symbol_upper: # Handle old format
                continue # Skip this stock
            new_user_stocks.append(s)

        if len(new_user_stocks) < original_len:
            if not new_user_stocks:
                del stocks_data[user_id_str]
            else:
                stocks_data[user_id_str] = new_user_stocks
            return _save_json(TRACKED_STOCKS_FILE, stocks_data)
    return False

def get_user_tracked_stocks(user_id):
    """
    Gets all tracked stocks for a specific user.
    Returns a list of dictionaries, e.g.,
    [{'symbol': 'AAPL', 'quantity': 10, 'purchase_price': 150.00}, {'symbol': 'MSFT'}]
    """
    user_id_str = str(user_id)
    stocks_data = _load_json(TRACKED_STOCKS_FILE)
    user_stocks_raw = stocks_data.get(user_id_str, [])
    
    # Ensure all entries are in the new dict format for consistency
    processed_stocks = []
    for s_entry in user_stocks_raw:
        if isinstance(s_entry, str):
            processed_stocks.append({"symbol": s_entry.upper()})
        elif isinstance(s_entry, dict) and "symbol" in s_entry:
            # Ensure symbol is uppercase
            s_entry["symbol"] = s_entry["symbol"].upper()
            # Ensure quantity and purchase_price are floats if they exist
            if "quantity" in s_entry and s_entry["quantity"] is not None:
                try:
                    s_entry["quantity"] = float(s_entry["quantity"])
                except ValueError: # Should not happen if saved correctly
                    s_entry.pop("quantity", None)
            if "purchase_price" in s_entry and s_entry["purchase_price"] is not None:
                try:
                    s_entry["purchase_price"] = float(s_entry["purchase_price"])
                except ValueError: # Should not happen if saved correctly
                    s_entry.pop("purchase_price", None)
            processed_stocks.append(s_entry)
            
    return processed_stocks

# --- Stock Alerts ---

def add_stock_alert(user_id, stock_symbol,
                    target_above=None, target_below=None,
                    dpc_above_target=None, dpc_below_target=None,
                    clear_above=False, clear_below=False,
                    clear_dpc_above=False, clear_dpc_below=False):
    """
    Adds or updates a stock alert for a user, including price and daily percentage change (DPC) targets.
    - target_above/target_below: The price for the price alert.
    - dpc_above_target/dpc_below_target: The percentage for the DPC alert.
    - clear_above/clear_below: Flags to clear the respective price alert.
    - clear_dpc_above/clear_dpc_below: Flags to clear the respective DPC alert.
    """
    user_id_str = str(user_id)
    symbol_upper = stock_symbol.upper()
    alerts = _load_json(STOCK_ALERTS_FILE)

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
    if clear_above:
        if user_stock_alerts.get("target_above") is not None:
            user_stock_alerts["target_above"] = None
            user_stock_alerts["active_above"] = False
            updated = True
    elif target_above is not None:
        try:
            price_above = float(target_above)
            if user_stock_alerts.get("target_above") != price_above or not user_stock_alerts.get("active_above"):
                user_stock_alerts["target_above"] = price_above
                user_stock_alerts["active_above"] = True
                updated = True
        except ValueError:
            pass # Invalid price, do nothing

    if clear_below:
        if user_stock_alerts.get("target_below") is not None:
            user_stock_alerts["target_below"] = None
            user_stock_alerts["active_below"] = False
            updated = True
    elif target_below is not None:
        try:
            price_below = float(target_below)
            if user_stock_alerts.get("target_below") != price_below or not user_stock_alerts.get("active_below"):
                user_stock_alerts["target_below"] = price_below
                user_stock_alerts["active_below"] = True
                updated = True
        except ValueError:
            pass # Invalid price, do nothing

    # Handle DPC Above Target
    if clear_dpc_above:
        if user_stock_alerts.get("dpc_above_target") is not None:
            user_stock_alerts["dpc_above_target"] = None
            user_stock_alerts["dpc_above_active"] = False
            updated = True
    elif dpc_above_target is not None:
        try:
            percent_above = float(dpc_above_target)
            if user_stock_alerts.get("dpc_above_target") != percent_above or not user_stock_alerts.get("dpc_above_active"):
                user_stock_alerts["dpc_above_target"] = percent_above
                user_stock_alerts["dpc_above_active"] = True
                updated = True
        except ValueError:
            pass # Invalid percentage

    # Handle DPC Below Target
    if clear_dpc_below:
        if user_stock_alerts.get("dpc_below_target") is not None:
            user_stock_alerts["dpc_below_target"] = None
            user_stock_alerts["dpc_below_active"] = False
            updated = True
    elif dpc_below_target is not None:
        try:
            percent_below = float(dpc_below_target) # Store as positive, interpretation is "below"
            if user_stock_alerts.get("dpc_below_target") != percent_below or not user_stock_alerts.get("dpc_below_active"):
                user_stock_alerts["dpc_below_target"] = percent_below
                user_stock_alerts["dpc_below_active"] = True
                updated = True
        except ValueError:
            pass # Invalid percentage
            
    if updated:
        # Clean up entry if all targets are None
        if (user_stock_alerts["target_above"] is None and
            user_stock_alerts["target_below"] is None and
            user_stock_alerts["dpc_above_target"] is None and
            user_stock_alerts["dpc_below_target"] is None):
            del alerts[user_id_str][symbol_upper]
            if not alerts[user_id_str]: # If user has no more alerts, remove user key
                del alerts[user_id_str]
        return _save_json(STOCK_ALERTS_FILE, alerts)
    return False # No change or error

def get_stock_alert(user_id, stock_symbol):
    """Gets the alert settings for a specific stock for a user."""
    user_id_str = str(user_id)
    symbol_upper = stock_symbol.upper()
    alerts = _load_json(STOCK_ALERTS_FILE)
    return alerts.get(user_id_str, {}).get(symbol_upper, None)

def remove_stock_alert_target(user_id, stock_symbol, direction):
    """
    Removes a specific price target (above or below) for a stock alert.
    Sets the target to None and active to False.
    `direction` should be 'above' or 'below'.
    This is an older way; prefer using add_stock_alert with clear_above/clear_below flags.
    """
    user_id_str = str(user_id)
    symbol_upper = stock_symbol.upper()
    alerts = _load_json(STOCK_ALERTS_FILE)

    if user_id_str in alerts and symbol_upper in alerts[user_id_str]:
        alert_data = alerts[user_id_str][symbol_upper]
        changed = False
        if direction == "above" and alert_data.get("target_above") is not None:
            alert_data["target_above"] = None
            alert_data["active_above"] = False
            changed = True
        elif direction == "below" and alert_data.get("target_below") is not None:
            alert_data["target_below"] = None
            alert_data["active_below"] = False
            changed = True
        
        if changed:
            # If all targets (price and DPC) are None, remove the stock entry
            if (alert_data.get("target_above") is None and
                alert_data.get("target_below") is None and
                alert_data.get("dpc_above_target") is None and
                alert_data.get("dpc_below_target") is None):
                del alerts[user_id_str][symbol_upper]
                if not alerts[user_id_str]: # If user has no more alerts, remove user key
                    del alerts[user_id_str]
            return _save_json(STOCK_ALERTS_FILE, alerts)
    return False

def deactivate_stock_alert_target(user_id, stock_symbol, direction):
    """
    Deactivates a specific target (price or DPC) (e.g., after it has been triggered).
    `direction` should be 'above', 'below', 'dpc_above', or 'dpc_below'.
    """
    user_id_str = str(user_id)
    symbol_upper = stock_symbol.upper()
    alerts = _load_json(STOCK_ALERTS_FILE)

    if user_id_str in alerts and symbol_upper in alerts[user_id_str]:
        alert_data = alerts[user_id_str][symbol_upper]
        changed = False
        if direction == "above" and alert_data.get("active_above", False):
            alert_data["active_above"] = False
            changed = True
        elif direction == "below" and alert_data.get("active_below", False):
            alert_data["active_below"] = False
            changed = True
        elif direction == "dpc_above" and alert_data.get("dpc_above_active", False):
            alert_data["dpc_above_active"] = False
            changed = True
        elif direction == "dpc_below" and alert_data.get("dpc_below_active", False):
            alert_data["dpc_below_active"] = False
            changed = True
        
        if changed:
            return _save_json(STOCK_ALERTS_FILE, alerts)
    return False

def get_all_active_alerts_for_monitoring():
    """
    Gets all stock alerts that are currently active for monitoring.
    Returns a list of dicts:
    [{'user_id': user_id, 'symbol': symbol,
      'target_above': price, 'active_above': True,
      'target_below': price, 'active_below': True,
      'dpc_above_target': percent, 'dpc_above_active': True,
      'dpc_below_target': percent, 'dpc_below_active': True
      }, ...]
    Only includes alerts where at least one price or DPC direction is active.
    """
    all_alerts_data = _load_json(STOCK_ALERTS_FILE)
    active_alerts_list = []
    for user_id_str, user_alerts in all_alerts_data.items():
        for symbol, alert_details in user_alerts.items():
            # Price targets
            target_above = alert_details.get("target_above")
            try: target_above = float(target_above) if target_above is not None else None
            except (ValueError, TypeError): target_above = None
            active_above = alert_details.get("active_above", False) and target_above is not None

            target_below = alert_details.get("target_below")
            try: target_below = float(target_below) if target_below is not None else None
            except (ValueError, TypeError): target_below = None
            active_below = alert_details.get("active_below", False) and target_below is not None

            # DPC targets
            dpc_above_target = alert_details.get("dpc_above_target")
            try: dpc_above_target = float(dpc_above_target) if dpc_above_target is not None else None
            except (ValueError, TypeError): dpc_above_target = None
            dpc_above_active = alert_details.get("dpc_above_active", False) and dpc_above_target is not None

            dpc_below_target = alert_details.get("dpc_below_target")
            try: dpc_below_target = float(dpc_below_target) if dpc_below_target is not None else None
            except (ValueError, TypeError): dpc_below_target = None
            dpc_below_active = alert_details.get("dpc_below_active", False) and dpc_below_target is not None

            if active_above or active_below or dpc_above_active or dpc_below_active:
                active_alerts_list.append({
                    "user_id": int(user_id_str),
                    "symbol": symbol,
                    "target_above": target_above if active_above else None,
                    "active_above": active_above,
                    "target_below": target_below if active_below else None,
                    "active_below": active_below,
                    "dpc_above_target": dpc_above_target if dpc_above_active else None,
                    "dpc_above_active": dpc_above_active,
                    "dpc_below_target": dpc_below_target if dpc_below_active else None,
                    "dpc_below_active": dpc_below_active
                })
    return active_alerts_list

def get_user_all_stock_alerts(user_id):
    """Gets all stock alert configurations for a specific user."""
    user_id_str = str(user_id)
    alerts = _load_json(STOCK_ALERTS_FILE)
    return alerts.get(user_id_str, {})

# --- User Preferences ---

def get_user_all_preferences(user_id):
    """Retrieves all preferences for a specific user."""
    user_id_str = str(user_id)
    preferences_data = _load_json(USER_PREFERENCES_FILE)
    return preferences_data.get(user_id_str, {})

def get_user_preference(user_id, key, default=None):
    """
    Retrieves a specific preference for a user.
    Returns the default value if the user or key is not found.
    """
    user_id_str = str(user_id)
    preferences_data = _load_json(USER_PREFERENCES_FILE)
    
    user_prefs = preferences_data.get(user_id_str, {})
    return user_prefs.get(key, default)

def set_user_preference(user_id, key, value):
    """
    Sets a specific preference for a user and saves it.
    """
    user_id_str = str(user_id)
    preferences_data = _load_json(USER_PREFERENCES_FILE)
    
    if user_id_str not in preferences_data:
        preferences_data[user_id_str] = {}
    
    preferences_data[user_id_str][key] = value
    return _save_json(USER_PREFERENCES_FILE, preferences_data)

if __name__ == '__main__':
    # Example Usage
    print("Data Manager - Example Usage")
    # TV Shows
    add_tv_subscription(123, 71712, "The Orville")
    add_tv_subscription(123, 1399, "Game of Thrones")
    add_tv_subscription(456, 71712, "The Orville")
    print("User 123 TV Subs:", get_user_tv_subscriptions(123))
    # Example new structure for episode_details
    sample_episode_details = {
        "id": 101, "name": "Pilot", "season_number": 1,
        "episode_number": 1, "air_date": "2017-09-10"
    }
    update_last_notified_episode_details(123, 71712, sample_episode_details)
    print("User 123 TV Subs after update:", get_user_tv_subscriptions(123))
    remove_tv_subscription(123, 1399)
    print("User 123 TV Subs after removal:", get_user_tv_subscriptions(123))
    print("All TV Subs:", get_all_tv_subscriptions())

    # Movie Subscriptions
    print("\n--- Movie Subscriptions Examples ---")
    add_movie_subscription(789, 550, "Fight Club", "1999-10-15")
    add_movie_subscription(789, 680, "Pulp Fiction", "1994-10-14")
    add_movie_subscription(101, 550, "Fight Club", "1999-10-15")
    print("User 789 Movie Subs:", get_user_movie_subscriptions(789))
    update_movie_notified_status(789, 550, True)
    print("User 789 Movie Subs after update:", get_user_movie_subscriptions(789))
    remove_movie_subscription(789, 680)
    print("User 789 Movie Subs after removal:", get_user_movie_subscriptions(789))
    print("All Movie Subs:", get_all_movie_subscriptions())


    # Stocks
    print("\n--- Tracked Stocks Examples ---")
    print("Track AAPL for user 123 (no portfolio):", add_tracked_stock(123, "AAPL"))
    print("Track MSFT for user 123 (with portfolio):", add_tracked_stock(123, "MSFT", quantity=10, purchase_price=250.00))
    print("Track GOOGL for user 456 (no portfolio):", add_tracked_stock(456, "GOOGL"))
    print("User 123 Tracked Stocks:", get_user_tracked_stocks(123))
    
    print("Update MSFT for user 123 (new quantity/price):", add_tracked_stock(123, "MSFT", quantity=15, purchase_price=255.50))
    print("User 123 Tracked Stocks after MSFT update:", get_user_tracked_stocks(123))

    print("Track TSLA for user 123 (with portfolio):", add_tracked_stock(123, "TSLA", quantity=5, purchase_price=700.00))
    print("User 123 Tracked Stocks:", get_user_tracked_stocks(123))

    print("Attempt to track MSFT again for user 123 (no new portfolio data):", add_tracked_stock(123, "MSFT"))
    print("User 123 Tracked Stocks (MSFT portfolio data should persist):", get_user_tracked_stocks(123))

    print("Remove MSFT for user 123:", remove_tracked_stock(123, "MSFT"))
    print("User 123 Tracked Stocks after MSFT removal:", get_user_tracked_stocks(123))
    
    print("Track NVDA for user 123 (invalid quantity):", add_tracked_stock(123, "NVDA", quantity="abc", purchase_price=300))
    print("User 123 Tracked Stocks (NVDA should not be added with portfolio):", get_user_tracked_stocks(123))
    print("Track NVDA for user 123 (correctly):", add_tracked_stock(123, "NVDA", quantity=2, purchase_price=300))
    print("User 123 Tracked Stocks:", get_user_tracked_stocks(123))


    print("All Tracked Stocks:", _load_json(TRACKED_STOCKS_FILE))

    print("\n--- Stock Alerts Examples ---")
    # Test add_stock_alert
    print("Adding AAPL alert for user 789 (above 150):", add_stock_alert(789, "AAPL", target_above=150))
    print("Adding MSFT alert for user 789 (below 200):", add_stock_alert(789, "MSFT", target_below=200))
    print("Adding GOOG alert for user 789 (above 2500, below 2400):", add_stock_alert(789, "GOOG", target_above=2500, target_below=2400))
    print("User 789 AAPL alert:", get_stock_alert(789, "AAPL"))
    print("User 789 MSFT alert:", get_stock_alert(789, "MSFT"))
    print("User 789 GOOG alert:", get_stock_alert(789, "GOOG"))

    # Test update alert
    print("Updating AAPL alert for user 789 (above 155):", add_stock_alert(789, "AAPL", target_above=155))
    print("User 789 AAPL alert after update:", get_stock_alert(789, "AAPL"))

    # Test clear alert target
    print("Clearing AAPL above alert for user 789:", add_stock_alert(789, "AAPL", clear_above=True))
    print("User 789 AAPL alert after clearing above:", get_stock_alert(789, "AAPL")) # dpc targets should remain if set
    
    print("Adding TSLA alert for user 789 (below 600):", add_stock_alert(789, "TSLA", target_below=600))
    print("User 789 TSLA alert:", get_stock_alert(789, "TSLA"))

    print("Adding NVDA alert for user 101 (above 300):", add_stock_alert(101, "NVDA", target_above=300))

    print("\n--- DPC Stock Alerts Examples ---")
    # Test add_stock_alert with DPC
    print("Adding AAPL DPC alert for user 789 (dpc_above 5%):", add_stock_alert(789, "AAPL", dpc_above_target=5))
    print("User 789 AAPL alert with DPC:", get_stock_alert(789, "AAPL"))
    print("Adding MSFT DPC alert for user 789 (dpc_below 3.5%):", add_stock_alert(789, "MSFT", dpc_below_target=3.5))
    print("User 789 MSFT alert with DPC:", get_stock_alert(789, "MSFT"))
    print("Adding GOOG DPC alert for user 789 (dpc_above 2%, dpc_below 2%):", add_stock_alert(789, "GOOG", dpc_above_target=2, dpc_below_target=2))
    print("User 789 GOOG alert with DPC:", get_stock_alert(789, "GOOG"))

    # Test updating DPC alert
    print("Updating AAPL DPC alert for user 789 (dpc_above 5.5%):", add_stock_alert(789, "AAPL", dpc_above_target=5.5))
    print("User 789 AAPL alert after DPC update:", get_stock_alert(789, "AAPL"))

    # Test clearing DPC alert target
    print("Clearing AAPL dpc_above alert for user 789:", add_stock_alert(789, "AAPL", clear_dpc_above=True))
    print("User 789 AAPL alert after clearing dpc_above:", get_stock_alert(789, "AAPL"))

    # Test get_all_active_alerts_for_monitoring with DPC
    print("\nAll active alerts for monitoring (including DPC):")
    for alert in get_all_active_alerts_for_monitoring():
        print(f"  {alert}")
    print("\n--- User Preferences Examples ---")
    print("Set user 1001 tv_overview to False:", set_user_preference(1001, "tv_show_dm_overview", False))
    print("Get user 1001 tv_overview (expected False):", get_user_preference(1001, "tv_show_dm_overview", True))
    print("Set user 1001 dnd_enabled to True:", set_user_preference(1001, "dnd_enabled", True))
    print("Set user 1001 dnd_start to 22:00:", set_user_preference(1001, "dnd_start_time", "22:00"))
    print("Set user 1001 dnd_end to 07:00:", set_user_preference(1001, "dnd_end_time", "07:00"))
    print("Get user 1001 dnd_enabled (expected True):", get_user_preference(1001, "dnd_enabled", False))
    print("Get user 1001 dnd_start (expected 22:00):", get_user_preference(1001, "dnd_start_time"))
    print("Get user 1002 non_existent_pref (expected 'default_val'):", get_user_preference(1002, "non_existent_pref", "default_val"))
    print("All User Preferences:", _load_json(USER_PREFERENCES_FILE))

    # Test deactivate_stock_alert_target for DPC
    print("Deactivating GOOG dpc_above alert for user 789:", deactivate_stock_alert_target(789, "GOOG", "dpc_above"))
    print("User 789 GOOG alert after deactivating dpc_above:", get_stock_alert(789, "GOOG"))
    print("\nAll active alerts after DPC deactivation:")
    for alert in get_all_active_alerts_for_monitoring():
        print(f"  {alert}")

    # Test clearing all targets for a stock (price and DPC)
    print("Clearing MSFT dpc_below alert for user 789:", add_stock_alert(789, "MSFT", clear_dpc_below=True))
    print("User 789 MSFT alert after clearing dpc_below:", get_stock_alert(789, "MSFT"))
    print("Clearing MSFT target_below alert for user 789:", add_stock_alert(789, "MSFT", clear_below=True))
    print("User 789 MSFT alert after clearing all targets:", get_stock_alert(789, "MSFT")) # Should be None

    print("\nFinal all alerts data:", _load_json(STOCK_ALERTS_FILE))

    # Clean up example files (optional, uncomment to use)
    # if os.path.exists(TV_SUBSCRIPTIONS_FILE): os.remove(TV_SUBSCRIPTIONS_FILE)
    # if os.path.exists(TRACKED_STOCKS_FILE): os.remove(TRACKED_STOCKS_FILE)
    # if os.path.exists(STOCK_ALERTS_FILE): os.remove(STOCK_ALERTS_FILE)
    # if os.path.exists(DATA_DIR) and not os.listdir(DATA_DIR): os.rmdir(DATA_DIR)
    print("\nExample usage complete. Check data/ directory for json files.")