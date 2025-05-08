# data_manager.py

import json
import os

DATA_DIR = "data"
TV_SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "tv_subscriptions.json")
TRACKED_STOCKS_FILE = os.path.join(DATA_DIR, "tracked_stocks.json")

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
            "last_notified_episode_id": None # Or a more specific initial value
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

def update_last_notified_episode(user_id, show_id, episode_id):
    """Updates the last notified episode ID for a user's specific show subscription."""
    user_id_str = str(user_id)
    subscriptions = _load_json(TV_SUBSCRIPTIONS_FILE)
    updated = False
    if user_id_str in subscriptions:
        for sub in subscriptions[user_id_str]:
            if sub['show_id'] == show_id:
                sub['last_notified_episode_id'] = episode_id
                updated = True
                break
    if updated:
        return _save_json(TV_SUBSCRIPTIONS_FILE, subscriptions)
    return False

# --- Tracked Stocks --- (Simplified for now)

def add_tracked_stock(user_id, stock_symbol):
    """Adds a stock symbol to a user's tracked list."""
    user_id_str = str(user_id)
    stocks = _load_json(TRACKED_STOCKS_FILE)
    
    if user_id_str not in stocks:
        stocks[user_id_str] = []
    
    # Avoid duplicates
    if stock_symbol.upper() not in [s.upper() for s in stocks[user_id_str]]:
        stocks[user_id_str].append(stock_symbol.upper())
        return _save_json(TRACKED_STOCKS_FILE, stocks)
    return False

def remove_tracked_stock(user_id, stock_symbol):
    """Removes a stock symbol from a user's tracked list."""
    user_id_str = str(user_id)
    stocks = _load_json(TRACKED_STOCKS_FILE)
    
    if user_id_str in stocks:
        symbol_upper = stock_symbol.upper()
        original_len = len(stocks[user_id_str])
        stocks[user_id_str] = [s for s in stocks[user_id_str] if s.upper() != symbol_upper]
        if len(stocks[user_id_str]) < original_len:
            if not stocks[user_id_str]:
                del stocks[user_id_str]
            return _save_json(TRACKED_STOCKS_FILE, stocks)
    return False

def get_user_tracked_stocks(user_id):
    """Gets all tracked stock symbols for a specific user."""
    user_id_str = str(user_id)
    stocks = _load_json(TRACKED_STOCKS_FILE)
    return stocks.get(user_id_str, [])

if __name__ == '__main__':
    # Example Usage
    print("Data Manager - Example Usage")
    # TV Shows
    add_tv_subscription(123, 71712, "The Orville")
    add_tv_subscription(123, 1399, "Game of Thrones")
    add_tv_subscription(456, 71712, "The Orville")
    print("User 123 TV Subs:", get_user_tv_subscriptions(123))
    update_last_notified_episode(123, 71712, 101)
    print("User 123 TV Subs after update:", get_user_tv_subscriptions(123))
    remove_tv_subscription(123, 1399)
    print("User 123 TV Subs after removal:", get_user_tv_subscriptions(123))
    print("All TV Subs:", get_all_tv_subscriptions())

    # Stocks
    add_tracked_stock(123, "AAPL")
    add_tracked_stock(123, "MSFT")
    add_tracked_stock(456, "GOOGL")
    print("User 123 Tracked Stocks:", get_user_tracked_stocks(123))
    remove_tracked_stock(123, "MSFT")
    print("User 123 Tracked Stocks after removal:", get_user_tracked_stocks(123))
    print("All Tracked Stocks:", _load_json(TRACKED_STOCKS_FILE))

    # Clean up example files
    # if os.path.exists(TV_SUBSCRIPTIONS_FILE):
    #     os.remove(TV_SUBSCRIPTIONS_FILE)
    # if os.path.exists(TRACKED_STOCKS_FILE):
    #     os.remove(TRACKED_STOCKS_FILE)
    # if os.path.exists(DATA_DIR) and not os.listdir(DATA_DIR):
    #      os.rmdir(DATA_DIR)
    print("Example usage complete. Check data/ directory for json files.")