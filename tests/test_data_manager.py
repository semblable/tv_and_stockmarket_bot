# tests/test_data_manager.py
import pytest
from data_manager import DataManager

# Tests for Tracked Stocks
def test_add_tracked_stock(db_manager):
    user_id = 12345
    symbol = "AAPL"
    
    # Test adding a new stock
    success = db_manager.add_tracked_stock(user_id, symbol)
    assert success is True
    
    stocks = db_manager.get_user_tracked_stocks(user_id)
    assert len(stocks) == 1
    assert stocks[0]['symbol'] == "AAPL"
    assert stocks[0]['quantity'] is None # Default

    # Test updating with portfolio data (UPSERT)
    success = db_manager.add_tracked_stock(user_id, symbol, quantity=10, purchase_price=150.0)
    assert success is True
    
    stocks = db_manager.get_user_tracked_stocks(user_id)
    assert len(stocks) == 1
    assert stocks[0]['symbol'] == "AAPL"
    assert stocks[0]['quantity'] == 10
    assert stocks[0]['purchase_price'] == 150.0

def test_remove_tracked_stock(db_manager):
    user_id = 12345
    symbol = "MSFT"
    db_manager.add_tracked_stock(user_id, symbol)
    
    success = db_manager.remove_tracked_stock(user_id, symbol)
    assert success is True
    
    stocks = db_manager.get_user_tracked_stocks(user_id)
    assert len(stocks) == 0

# Tests for Stock Alerts
def test_add_stock_alert(db_manager):
    user_id = 12345
    symbol = "TSLA"
    db_manager.add_tracked_stock(user_id, symbol) 
    
    success = db_manager.add_stock_alert(user_id, symbol, target_above=200.0)
    assert success is True
    
    alert = db_manager.get_stock_alert(user_id, symbol)
    assert alert is not None
    assert alert['target_above'] == 200.0
    assert alert['active_above'] == 1

    # Update alert
    success = db_manager.add_stock_alert(user_id, symbol, target_below=100.0)
    alert = db_manager.get_stock_alert(user_id, symbol)
    assert alert['target_above'] == 200.0 # Should persist
    assert alert['target_below'] == 100.0
    assert alert['active_below'] == 1

    # Clear alert
    success = db_manager.add_stock_alert(user_id, symbol, clear_above=True)
    alert = db_manager.get_stock_alert(user_id, symbol)
    assert alert['active_above'] == 0

# Tests for TV Subscriptions
def test_tv_subscriptions(db_manager):
    user_id = 999
    show_id = 123
    show_name = "Test Show"
    poster_path = "/path/to/poster.jpg"
    
    success = db_manager.add_tv_show_subscription(user_id, show_id, show_name, poster_path)
    assert success is True
    
    subs = db_manager.get_user_tv_subscriptions(user_id)
    assert len(subs) == 1
    assert subs[0]['show_tmdb_id'] == show_id
    assert subs[0]['show_name'] == show_name
    
    success = db_manager.remove_tv_show_subscription(user_id, show_id)
    assert success is True
    assert len(db_manager.get_user_tv_subscriptions(user_id)) == 0


def test_book_author_subscriptions(db_manager):
    guild_id = 111
    user_id = 222
    author_id = "OL23919A"
    author_name = "Agatha Christie"

    ok = db_manager.add_book_author_subscription(guild_id, user_id, author_id, author_name, None)
    assert ok is True

    subs = db_manager.get_user_book_author_subscriptions(guild_id, user_id)
    assert len(subs) == 1
    assert subs[0]["author_id"] == author_id
    assert subs[0]["author_name"] == author_name

    ok = db_manager.mark_author_work_seen(author_id, "OL82563W")
    assert ok is True
    seen = db_manager.get_seen_work_ids_for_author(author_id)
    assert "OL82563W" in seen

    ok = db_manager.remove_book_author_subscription(guild_id, user_id, author_id)
    assert ok is True
    subs2 = db_manager.get_user_book_author_subscriptions(guild_id, user_id)
    assert len(subs2) == 0
