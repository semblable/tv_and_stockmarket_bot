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


def test_data_manager_multi_webhook_support(db_manager):
    user_id = 98765

    # 1. Simulate legacy token already stored in DB
    db_manager.set_user_preference(user_id, "report_webhook_token", "legacy_token_123")

    # 2. get_user_webhooks should include "default" -> legacy_token_123
    hooks = db_manager.get_user_webhooks(user_id)
    assert "default" in hooks
    assert hooks["default"] == "legacy_token_123"

    # 3. Create a second webhook for linguaread
    db_manager.set_user_webhook(user_id, "linguaread", "lingua_token_456")
    hooks = db_manager.get_user_webhooks(user_id)
    assert len(hooks) == 2
    assert hooks["default"] == "legacy_token_123"
    assert hooks["linguaread"] == "lingua_token_456"

    # 4. Token lookup resolves user_id for both tokens
    assert db_manager.get_user_id_for_report_token("legacy_token_123") == str(user_id)
    assert db_manager.get_user_id_for_report_token("lingua_token_456") == str(user_id)
    assert db_manager.get_user_id_for_report_token("unknown_token") is None

    # 5. Deleting linguaread preserves default
    deleted = db_manager.delete_user_webhook(user_id, "linguaread")
    assert deleted is True
    hooks = db_manager.get_user_webhooks(user_id)
    assert "linguaread" not in hooks
    assert "default" in hooks
    assert db_manager.get_user_id_for_report_token("legacy_token_123") == str(user_id)
    assert db_manager.get_user_id_for_report_token("lingua_token_456") is None


@pytest.mark.asyncio
async def test_webhook_commands_multiple_tokens(db_manager):
    import asyncio
    bot = MagicMock()
    bot.loop = asyncio.get_event_loop()
    cog = SettingsCog(bot, db_manager)

    ctx = MagicMock()
    ctx.author.id = 55555
    ctx.send = AsyncMock()
    ctx.interaction = None

    # 1. Generate default link
    await cog.webhook_link.callback(cog, ctx, name="default", reset=False)
    assert ctx.send.called
    msg1 = ctx.send.call_args[0][0]
    assert "Your report webhook link (`default`)" in msg1

    # Extract default token
    hooks = db_manager.get_user_webhooks(55555)
    default_token = hooks["default"]
    assert default_token in msg1

    # 2. Generate linguaread link without rotating default
    ctx.send.reset_mock()
    await cog.webhook_link.callback(cog, ctx, name="linguaread", reset=False)
    assert ctx.send.called
    msg2 = ctx.send.call_args[0][0]
    assert "Your report webhook link (`linguaread`)" in msg2

    hooks = db_manager.get_user_webhooks(55555)
    assert len(hooks) == 2
    assert hooks["default"] == default_token
    assert "linguaread" in hooks
    lingua_token = hooks["linguaread"]
    assert lingua_token != default_token

    # 3. List webhooks
    ctx.send.reset_mock()
    await cog.webhook_list.callback(cog, ctx)
    assert ctx.send.called
    list_msg = ctx.send.call_args[0][0]
    assert "Your Active Webhooks (2)" in list_msg
    assert "default" in list_msg
    assert "linguaread" in list_msg

    # 4. Delete linguaread webhook
    ctx.send.reset_mock()
    await cog.webhook_delete.callback(cog, ctx, name="linguaread")
    assert ctx.send.called
    assert "has been deleted" in ctx.send.call_args[0][0]

    hooks = db_manager.get_user_webhooks(55555)
    assert len(hooks) == 1
    assert "default" in hooks
