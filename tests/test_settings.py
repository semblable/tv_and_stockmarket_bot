import pytest
from unittest.mock import MagicMock
from cogs.settings import timezone_autocomplete, POPULAR_TIMEZONES


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
