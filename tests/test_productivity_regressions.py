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


@pytest.mark.asyncio
async def test_vacation_pauses_habits_across_scopes(mock_bot, db_manager):
    """
    Regression test: `/vacation` should pause the user's habits even if invoked from a server
    whose guild_id doesn't match where the habits were created (or if they were created in DMs).
    """
    from cogs.productivity import ProductivityCog

    user_id = 424242
    habit_id = db_manager.create_habit(
        123,  # habit guild scope
        user_id,
        "Test habit",
        [0, 1, 2, 3, 4],
        "18:00",
        "UTC",
        True,
        "2000-01-01 00:00:00",
    )
    assert isinstance(habit_id, int)

    class _Author:
        def __init__(self, uid: int):
            self.id = uid

    class _Guild:
        def __init__(self, gid: int):
            self.id = gid

    class _Ctx:
        def __init__(self):
            self.guild = _Guild(999)  # different from habit's guild_id
            self.author = _Author(user_id)
            self.interaction = None
            self._sent = []

        async def send(self, content=None, **kwargs):
            self._sent.append((content, kwargs))
            return None

    cog = ProductivityCog(mock_bot, db_manager=db_manager)
    ctx = _Ctx()

    # Call the command callback directly (hybrid_command wraps it).
    await cog.vacation.callback(cog, ctx, 3, "")

    h = db_manager.get_habit(123, user_id, int(habit_id))
    assert h is not None
    assert h.get("paused_until") is not None


