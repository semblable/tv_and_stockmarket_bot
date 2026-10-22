import pytest
import gzip
from unittest.mock import AsyncMock, MagicMock, patch
from cogs.settings import timezone_autocomplete, POPULAR_TIMEZONES, _generate_backup_archive, SettingsCog
import config


@pytest.mark.asyncio
async def test_timezone_autocomplete_empty_query():
    mock_interaction = MagicMock()
    choices = await timezone_autocomplete(mock_interaction, "")
    assert len(choices) == len(POPULAR_TIMEZONES)
    assert choices[0].name == "Europe/Warsaw"


@pytest.mark.asyncio
async def test_timezone_autocomplete_filtered_query():
    mock_interaction = MagicMock()
    choices = await timezone_autocomplete(mock_interaction, "york")
    assert len(choices) == 1
    assert choices[0].name == "America/New_York"
    assert choices[0].value == "America/New_York"


@pytest.mark.asyncio
async def test_timezone_autocomplete_no_match():
    mock_interaction = MagicMock()
    choices = await timezone_autocomplete(mock_interaction, "nonexistent_place_123")
    assert len(choices) == 0


def test_generate_backup_archive(db_manager):
    data = _generate_backup_archive(db_manager)
    assert len(data) > 0
    # Decompress to verify it's a valid SQLite archive
    decompressed = gzip.decompress(data)
    assert decompressed[:15] == b"SQLite format 3"


@pytest.mark.asyncio
async def test_backup_download_non_owner(db_manager):
    bot = MagicMock()
    cog = SettingsCog(bot, db_manager)
    cog.backup_download.cog = cog
    ctx = MagicMock()
    ctx.author.id = 999999999
    ctx.send = AsyncMock()

    with patch.object(config, "TIMER_OWNER_ID", 12345):
        await cog.backup_download.callback(cog, ctx)
        ctx.send.assert_called_once()
        assert "restricted to the bot owner" in ctx.send.call_args[0][0]


@pytest.mark.asyncio
async def test_backup_download_owner(db_manager):
    import asyncio
    bot = MagicMock()
    bot.loop = asyncio.get_event_loop()
    cog = SettingsCog(bot, db_manager)
    cog.backup_download.cog = cog
    ctx = MagicMock()
    ctx.author.id = 12345
    ctx.author.send = AsyncMock()
    ctx.send = AsyncMock()
    ctx.interaction = None

    with patch.object(config, "TIMER_OWNER_ID", 12345):
        await cog.backup_download.callback(cog, ctx)
        assert ctx.author.send.called
        assert "Database Backup" in ctx.author.send.call_args[1]["content"]
        assert len(ctx.author.send.call_args[1]["file"].fp.getvalue()) > 0


@pytest.mark.asyncio
async def test_monthly_backup_task_sends_on_first_of_month(db_manager):
    import asyncio
    import datetime
    bot = MagicMock()
    bot.loop = asyncio.get_event_loop()
    mock_owner = AsyncMock()
    bot.get_user.return_value = mock_owner
    cog = SettingsCog(bot, db_manager)

    fake_now = datetime.datetime(2026, 11, 1, 5, 0, tzinfo=datetime.timezone.utc)
    with patch("datetime.datetime") as mock_dt, patch.object(config, "TIMER_OWNER_ID", 12345):
        mock_dt.now.return_value = fake_now
        await cog.monthly_backup_task()

        # Should send DM to owner
        assert mock_owner.send.called
        assert "Monthly Database Backup — 2026-11-01" in mock_owner.send.call_args[1]["content"]

        # Preference should now be recorded
        pref = db_manager.get_user_preference(12345, "last_monthly_backup_sent")
        assert pref == "2026-11"

        # Calling again should not resend
        mock_owner.send.reset_mock()
        await cog.monthly_backup_task()
        assert not mock_owner.send.called
