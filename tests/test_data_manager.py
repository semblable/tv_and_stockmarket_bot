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


def test_habit_reminder_profile_default_and_set(db_manager):
    guild_id = 0
    user_id = 123
    habit_id = db_manager.create_habit(
        guild_id,
        user_id,
        "Test habit",
        [0, 1, 2, 3, 4],
        "18:00",
        "Europe/Warsaw",
        True,
        "2000-01-01 00:00:00",
    )
    assert isinstance(habit_id, int)

    h = db_manager.get_habit(guild_id, user_id, habit_id)
    assert h is not None
    assert (h.get("remind_profile") or "normal") == "normal"

    ok = db_manager.set_habit_reminder_profile(guild_id, user_id, habit_id, "aggressive")
    assert ok is True
    h2 = db_manager.get_habit(guild_id, user_id, habit_id)
    assert h2 is not None
    assert (h2.get("remind_profile") or "").lower() == "aggressive"

    due = db_manager.list_due_habit_reminders("2100-01-01 00:00:00", 50)
    # Our habit is due (next_due_at is ancient), so it should show up and include profile.
    assert any((r.get("id") == habit_id and (r.get("remind_profile") or "").lower() == "aggressive") for r in due)


def test_habit_reminder_profile_migration_from_old_schema(tmp_path, monkeypatch):
    """
    Create an older `habits` table without `remind_profile`, then ensure DataManagerCore migrates it.
    """
    import sqlite3

    db_path = tmp_path / "old_schema.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                days_of_week TEXT NOT NULL,
                due_time_local TEXT,
                tz_name TEXT,
                due_time_utc TEXT NOT NULL DEFAULT '18:00',
                remind_enabled INTEGER NOT NULL DEFAULT 1,
                remind_level INTEGER NOT NULL DEFAULT 0,
                next_due_at TIMESTAMP,
                next_remind_at TIMESTAMP,
                last_checkin_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO habits (guild_id, user_id, name, days_of_week, due_time_utc, remind_enabled, remind_level, next_due_at)
            VALUES ('0', '999', 'Old habit', '[0,1,2,3,4]', '18:00', 1, 0, '2000-01-01 00:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("data_manager.SQLITE_DB_PATH", str(db_path))
    mgr = DataManager()
    try:
        cols = mgr._execute_query("PRAGMA table_info(habits);", fetch_all=True)
        col_names = [c.get("name") for c in cols if isinstance(c, dict)]
        assert "remind_profile" in col_names

        row = mgr._execute_query("SELECT remind_profile FROM habits WHERE user_id = '999' AND id = 1;", fetch_one=True)
        assert row is not None
        assert (row.get("remind_profile") or "").lower() == "normal"
    finally:
        mgr.close()


def test_habit_snooze_excludes_from_due_list(db_manager):
    guild_id = 0
    user_id = 321
    habit_id = db_manager.create_habit(
        guild_id,
        user_id,
        "Snooze me",
        [0, 1, 2, 3, 4],
        "18:00",
        "Europe/Warsaw",
        True,
        "2000-01-01 00:00:00",
    )
    assert isinstance(habit_id, int)

    # Sanity: it's due far in the future 'now'
    due0 = db_manager.list_due_habit_reminders("2100-01-01 00:00:00", 50)
    assert any(r.get("id") == habit_id for r in due0)

    # Snooze for "today" at 2099-12-31 noon UTC. This should suppress reminders until the next
    # local midnight in the habit tz (Europe/Warsaw), returned as snoozed_until (UTC).
    res = db_manager.snooze_habit_for_day(guild_id, user_id, habit_id, "2099-12-31 12:00:00", "week", 1)
    assert res.get("ok") is True
    until_s = res.get("snoozed_until")
    assert isinstance(until_s, str) and len(until_s) >= 19

    # Before snooze expires => not due for reminders
    due1 = db_manager.list_due_habit_reminders("2099-12-31 22:00:00", 50)
    assert not any(r.get("id") == habit_id for r in due1)

    # At/after snooze expires, it should be due again (it's still overdue).
    due2 = db_manager.list_due_habit_reminders(until_s, 50)
    assert any(r.get("id") == habit_id for r in due2)


def test_habit_snooze_migration_from_old_schema(tmp_path, monkeypatch):
    """
    Create an older `habits` table without snooze columns, then ensure DataManagerCore migrates it.
    """
    import sqlite3

    db_path = tmp_path / "old_schema_snooze.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                days_of_week TEXT NOT NULL,
                due_time_local TEXT,
                tz_name TEXT,
                due_time_utc TEXT NOT NULL DEFAULT '18:00',
                remind_enabled INTEGER NOT NULL DEFAULT 1,
                remind_profile TEXT NOT NULL DEFAULT 'normal',
                remind_level INTEGER NOT NULL DEFAULT 0,
                next_due_at TIMESTAMP,
                next_remind_at TIMESTAMP,
                last_checkin_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO habits (guild_id, user_id, name, days_of_week, due_time_utc, remind_enabled, remind_level, next_due_at)
            VALUES ('0', '777', 'Old habit 2', '[0,1,2,3,4]', '18:00', 1, 0, '2000-01-01 00:00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr("data_manager.SQLITE_DB_PATH", str(db_path))
    mgr = DataManager()
    try:
        cols = mgr._execute_query("PRAGMA table_info(habits);", fetch_all=True)
        col_names = [c.get("name") for c in cols if isinstance(c, dict)]
        assert "snoozed_until" in col_names
        assert "last_snooze_at" in col_names
        assert "last_snooze_period" in col_names

        # Default for new column should exist (week)
        row = mgr._execute_query("SELECT last_snooze_period FROM habits WHERE user_id = '777' AND id = 1;", fetch_one=True)
        assert row is not None
        assert (row.get("last_snooze_period") or "").lower() == "week"
    finally:
        mgr.close()


def test_habit_edit_updates_name_and_clears_snooze(db_manager):
    guild_id = 0
    user_id = 555
    habit_id = db_manager.create_habit(
        guild_id,
        user_id,
        "Old name",
        [0, 1, 2, 3, 4],
        "18:00",
        "Europe/Warsaw",
        True,
        "2100-01-01 00:00:00",
    )
    assert isinstance(habit_id, int)

    # Snooze it to set snoozed_until, then "edit" schedule and clear snooze
    res = db_manager.snooze_habit_for_day(guild_id, user_id, habit_id, "2099-12-31 12:00:00", "week", 1)
    assert res.get("ok") is True

    h1 = db_manager.get_habit(guild_id, user_id, habit_id)
    assert h1 is not None
    assert h1.get("snoozed_until") is not None

    ok = db_manager.set_habit_schedule_and_due(
        guild_id,
        user_id,
        habit_id,
        name="New name",
        days_of_week=[0],  # only Mondays
        next_due_at_utc="2100-01-03 00:00:00",
        clear_snoozed_until=True,
        clear_next_remind_at=True,
        remind_level=0,
    )
    assert ok is True

    h2 = db_manager.get_habit(guild_id, user_id, habit_id)
    assert h2 is not None
    assert h2.get("name") == "New name"
    assert h2.get("snoozed_until") is None


def test_habit_edit_can_update_remind_profile_via_set_habit_schedule_and_due(db_manager):
    guild_id = 0
    user_id = 556
    habit_id = db_manager.create_habit(
        guild_id,
        user_id,
        "Profile test",
        [0, 1, 2, 3, 4],
        "18:00",
        "Europe/Warsaw",
        True,
        "2100-01-01 00:00:00",
    )
    assert isinstance(habit_id, int)

    ok = db_manager.set_habit_schedule_and_due(guild_id, user_id, habit_id, remind_profile="aggressive")
    assert ok is True
    h = db_manager.get_habit(guild_id, user_id, habit_id)
    assert h is not None
    assert (h.get("remind_profile") or "").lower() == "aggressive"


def test_habit_stats_streak_and_completion_rate(db_manager):
    """
    Deterministic habit stats test using UTC timezone and manually inserted checkins.
    """
    guild_id = 0
    user_id = 9001
    habit_id = db_manager.create_habit(
        guild_id,
        user_id,
        "Stats habit",
        [0, 1, 2, 3, 4],  # Mon-Fri
        "18:00",
        "UTC",
        True,
        "2000-01-01 00:00:00",
    )
    assert isinstance(habit_id, int)

    # Force tz_name to UTC for deterministic local-day bucketing.
    db_manager._execute_query(
        "UPDATE habits SET tz_name = 'UTC' WHERE guild_id = :guild_id AND user_id = :user_id AND id = :id",
        {"guild_id": str(guild_id), "user_id": str(user_id), "id": int(habit_id)},
        commit=True,
    )

    # Insert 5 consecutive weekday checkins (Mon..Fri)
    for day in ["2025-01-06", "2025-01-07", "2025-01-08", "2025-01-09", "2025-01-10"]:
        db_manager._execute_query(
            """
            INSERT INTO habit_checkins (habit_id, guild_id, user_id, checked_in_at, note)
            VALUES (:habit_id, :guild_id, :user_id, :checked_in_at, NULL)
            """,
            {
                "habit_id": int(habit_id),
                "guild_id": str(guild_id),
                "user_id": str(user_id),
                "checked_in_at": f"{day} 10:00:00",
            },
            commit=True,
        )

    stats = db_manager.get_habit_stats(guild_id, user_id, habit_id, days=7, now_utc="2025-01-10 12:00:00")
    assert isinstance(stats, dict)

    # In the last 7 days (2025-01-04..2025-01-10), scheduled days are Mon-Fri => 5.
    assert int(stats.get("scheduled_days") or 0) == 5
    assert int(stats.get("completed_days") or 0) == 5
    assert float(stats.get("completion_rate") or 0.0) == 1.0

    # Streaks over scheduled days only
    assert int(stats.get("current_streak") or 0) == 5
    assert int(stats.get("best_streak") or 0) >= 5


def test_todo_stats_counts_streak_and_avg_time(db_manager):
    guild_id = 0
    user_id = 4242

    # Insert 2 done tasks on consecutive days, plus 1 open task.
    # Note: we insert explicit created_at/done_at for deterministic stats.
    db_manager._execute_query(
        """
        INSERT INTO todo_items (guild_id, user_id, content, is_done, created_at, done_at, remind_enabled, remind_level, next_remind_at)
        VALUES ('0', :user_id, 't1', 1, '2025-01-01 10:00:00', '2025-01-01 12:00:00', 0, 0, NULL)
        """,
        {"user_id": str(user_id)},
        commit=True,
    )
    db_manager._execute_query(
        """
        INSERT INTO todo_items (guild_id, user_id, content, is_done, created_at, done_at, remind_enabled, remind_level, next_remind_at)
        VALUES ('0', :user_id, 't2', 1, '2025-01-02 09:00:00', '2025-01-02 21:00:00', 0, 0, NULL)
        """,
        {"user_id": str(user_id)},
        commit=True,
    )
    db_manager._execute_query(
        """
        INSERT INTO todo_items (guild_id, user_id, content, is_done, created_at, done_at, remind_enabled, remind_level, next_remind_at)
        VALUES ('0', :user_id, 'open', 0, '2025-01-02 08:00:00', NULL, 0, 0, NULL)
        """,
        {"user_id": str(user_id)},
        commit=True,
    )

    stats = db_manager.get_todo_stats(guild_id, user_id, days=3, now_utc="2025-01-03 12:00:00")
    assert isinstance(stats, dict)
    assert int(stats.get("open_count") or 0) == 1
    assert int(stats.get("done_count") or 0) == 2
    assert int(stats.get("total_count") or 0) == 3

    # Range 2025-01-01..2025-01-03 includes 2 created + 2 done + 1 extra created(open) => created_in_range=3, done_in_range=2
    assert int(stats.get("created_in_range") or 0) == 3
    assert int(stats.get("done_in_range") or 0) == 2

    # Done streak: last day (01-03) has 0 done => current streak 0, best streak 2
    assert int(stats.get("current_done_streak_days") or 0) == 0
    assert int(stats.get("best_done_streak_days") or 0) == 2

    # Avg time-to-done: (2h + 12h)/2 = 7h
    avg = stats.get("avg_hours_to_done")
    assert avg is not None
    assert abs(float(avg) - 7.0) < 0.01


def test_list_users_with_productivity_data_and_list_habits_any_scope(db_manager):
    # Two users, one with todo, one with habit
    db_manager._execute_query(
        """
        INSERT INTO todo_items (guild_id, user_id, content, is_done, created_at, done_at, remind_enabled, remind_level, next_remind_at)
        VALUES ('0', '101', 't', 0, '2025-01-01 00:00:00', NULL, 0, 0, NULL)
        """,
        commit=True,
    )
    hid = db_manager.create_habit(0, 202, "h", [0], "18:00", "UTC", True, "2025-01-01 00:00:00")
    assert isinstance(hid, int)

    uids = db_manager.list_users_with_productivity_data(100)
    assert 101 in uids
    assert 202 in uids

    habits = db_manager.list_habits_any_scope(202, 10)
    assert any(int(h.get("id") or 0) == hid for h in habits)
