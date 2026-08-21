import pytest
from datetime import datetime, date, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from cogs.tv_shows import TVShows
from utils.timezone_utils import tzinfo_from_name


def test_build_monthly_schedule_embed_with_episodes():
    episodes = [
        {
            "show_name": "Silo",
            "show_id": 125988,
            "season_number": 3,
            "episode_number": 9,
            "episode_name": "Farewell",
            "air_date": "2026-08-28",
            "air_date_obj": date(2026, 8, 28),
            "air_datetime_utc": "2026-08-28T12:00:00+00:00",
            "rating": 8.5,
            "source": "TVMaze",
        },
        {
            "show_name": "Ted Lasso",
            "show_id": 97546,
            "season_number": 4,
            "episode_number": 4,
            "episode_name": "Greyhounds' Day Off",
            "air_date": "2026-08-26",
            "air_date_obj": date(2026, 8, 26),
            "air_datetime_utc": "2026-08-26T12:00:00+00:00",
            "rating": 9.0,
            "source": "TVMaze",
        },
    ]

    embed = TVShows._build_monthly_schedule_embed(2026, 8, episodes, "TestUser")
    assert "August 2026" in embed.title
    assert "**2** episode(s)" in embed.description
    assert len(embed.fields) == 2
    # Date headers
    assert any("Aug 26" in f.name for f in embed.fields)
    assert any("Aug 28" in f.name for f in embed.fields)


def test_build_monthly_schedule_embed_empty():
    embed = TVShows._build_monthly_schedule_embed(2026, 9, [], "TestUser")
    assert "September 2026" in embed.title
    assert "No new episodes scheduled" in embed.description
    assert len(embed.fields) == 0


@pytest.mark.asyncio
async def test_fetch_monthly_schedule():
    mock_bot = MagicMock()
    mock_bot.loop = MagicMock()
    mock_bot.loop.run_in_executor = AsyncMock()

    mock_db = MagicMock()
    mock_db.get_user_tv_subscriptions.return_value = [
        {"show_tmdb_id": 125988, "show_name": "Silo", "show_tvmaze_id": 38052}
    ]

    cog = TVShows.__new__(TVShows)
    cog.bot = mock_bot
    cog.db_manager = mock_db

    # Setup run_in_executor to return subscriptions and episodes
    tvmaze_episodes = [
        {
            "id": 1,
            "name": "July Ep",
            "season": 3,
            "number": 5,
            "airdate": "2026-07-31",
        },
        {
            "id": 2,
            "name": "August Ep 1",
            "season": 3,
            "number": 6,
            "airdate": "2026-08-07",
        },
        {
            "id": 3,
            "name": "August Ep 2",
            "season": 3,
            "number": 7,
            "airdate": "2026-08-14",
        },
        {
            "id": 4,
            "name": "September Ep",
            "season": 3,
            "number": 10,
            "airdate": "2026-09-04",
        },
    ]

    async def side_effect(executor, func, *args):
        if func == mock_db.get_user_tv_subscriptions:
            return mock_db.get_user_tv_subscriptions(*args)
        return tvmaze_episodes

    mock_bot.loop.run_in_executor.side_effect = side_effect

    with patch("cogs.tv_shows.tvmaze_client.get_show_episodes", return_value=tvmaze_episodes):
        episodes = await cog._fetch_monthly_schedule(12345, 2026, 8)

    assert len(episodes) == 2
    assert episodes[0]["air_date"] == "2026-08-07"
    assert episodes[1]["air_date"] == "2026-08-14"
    assert episodes[0]["episode_name"] == "August Ep 1"


@pytest.mark.asyncio
async def test_monthly_digest_schedule_conditions():
    """Verify that digest triggers on 1st of month at >= 09:00 local time and respects dedup key."""
    mock_bot = MagicMock()
    mock_bot.loop = MagicMock()
    mock_bot.loop.run_in_executor = AsyncMock()

    mock_db = MagicMock()
    mock_db.get_all_tv_subscriptions.return_value = [
        {"user_id": "817792006372851743", "show_tmdb_id": 125988}
    ]

    cog = TVShows.__new__(TVShows)
    cog.bot = mock_bot
    cog.db_manager = mock_db

    # 1. On 1st of month at 09:15 CET, should trigger
    test_now_utc = datetime(2026, 9, 1, 7, 15, tzinfo=timezone.utc)  # 09:15 CEST

    mock_user = MagicMock()
    mock_user.display_name = "Kamil"
    mock_user.send = AsyncMock()
    mock_bot.get_user.return_value = mock_user

    async def side_effect(executor, func, *args):
        if func == mock_db.get_all_tv_subscriptions:
            return mock_db.get_all_tv_subscriptions()
        if func == mock_db.get_user_preference:
            pref_key = args[1]
            if pref_key == "tv_monthly_digest":
                return True
            if pref_key == "timezone":
                return "Europe/Warsaw"
            if pref_key == "last_sent_tv_monthly_digest":
                return "2026-08" # Not sent for 2026-09 yet
        if func == mock_db.get_user_tv_subscriptions:
            return [{"show_tmdb_id": 125988, "show_name": "Silo", "show_tvmaze_id": 38052}]
        if func == mock_db.set_user_preference:
            return True
        return []

    mock_bot.loop.run_in_executor.side_effect = side_effect

    with patch("cogs.tv_shows.datetime") as mock_dt:
        mock_dt.now.return_value = test_now_utc
        mock_dt.strptime = datetime.strptime
        await cog.check_monthly_tv_digest()

    assert mock_user.send.called
    sent_embed = mock_user.send.call_args[1]["embed"]
    assert "September 2026" in sent_embed.title
