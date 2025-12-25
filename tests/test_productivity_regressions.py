import pytest


def test_habit_checkins_executor_fn_calls_keyword_only_args(db_manager):
    """
    Regression test: `DataManager.list_habit_checkins` uses keyword-only args for
    `since_utc` and `limit`. Accidentally passing them positionally will raise TypeError,
    but that error only shows up at runtime (often inside `run_in_executor`).
    """
    from cogs.productivity import _habit_checkins_executor_fn

    fn = _habit_checkins_executor_fn(
        db_manager=db_manager,
        guild_id=0,
        user_id=123,
        habit_id=999999,  # doesn't need to exist; query should just return []
        since_utc="2000-01-01 00:00:00",
        limit=5000,
    )
    out = fn()
    assert isinstance(out, list)


