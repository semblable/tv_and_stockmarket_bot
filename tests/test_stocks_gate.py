import pytest
from unittest.mock import MagicMock
from discord.ext import commands as dpy_commands

from cogs.stocks import Stocks, PREF_STOCKS_ENABLED


def _make_cog(db_manager, mock_bot):
    # Bypass __init__ so the background tasks.loop tasks are not started.
    cog = Stocks.__new__(Stocks)
    cog.bot = mock_bot
    cog.db_manager = db_manager
    return cog


def _ctx(command_name, user_id=999):
    ctx = MagicMock()
    ctx.command.name = command_name
    ctx.author.id = user_id
    return ctx


@pytest.mark.asyncio
async def test_cog_check_allows_by_default(db_manager, mock_bot):
    cog = _make_cog(db_manager, mock_bot)
    assert await cog.cog_check(_ctx("stock_price")) is True


@pytest.mark.asyncio
async def test_cog_check_blocks_disabled_user(db_manager, mock_bot):
    cog = _make_cog(db_manager, mock_bot)
    db_manager.set_user_preference(999, PREF_STOCKS_ENABLED, False)
    with pytest.raises(dpy_commands.CheckFailure):
        await cog.cog_check(_ctx("stock_price"))


@pytest.mark.asyncio
async def test_cog_check_exempts_enable_disable(db_manager, mock_bot):
    cog = _make_cog(db_manager, mock_bot)
    db_manager.set_user_preference(999, PREF_STOCKS_ENABLED, False)
    # Even when disabled, the user can still re-enable / sync.
    assert await cog.cog_check(_ctx("stocks_enable")) is True
    assert await cog.cog_check(_ctx("stocks_disable")) is True
    assert await cog.cog_check(_ctx("sync_commands")) is True


@pytest.mark.asyncio
async def test_cog_check_is_per_user(db_manager, mock_bot):
    cog = _make_cog(db_manager, mock_bot)
    db_manager.set_user_preference(999, PREF_STOCKS_ENABLED, False)
    # A different user is unaffected.
    assert await cog.cog_check(_ctx("stock_price", user_id=111)) is True


@pytest.mark.asyncio
async def test_cog_check_fails_open_without_db(mock_bot):
    cog = Stocks.__new__(Stocks)
    cog.bot = mock_bot
    cog.db_manager = None
    assert await cog.cog_check(_ctx("stock_price")) is True
