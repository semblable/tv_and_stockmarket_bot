import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from api_clients import tvmaze_client, yahoo_finance_client, openweathermap_client

# --- TVMaze Tests ---

@patch('api_clients.tvmaze_client.requests.get')
def test_tvmaze_search_shows(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"show": {"id": 1, "name": "Test Show", "summary": "A test"}}
    ]
    mock_get.return_value = mock_response

    results = tvmaze_client.search_shows("Test")
    assert len(results) == 1
    assert results[0]['name'] == "Test Show"

@patch('api_clients.tvmaze_client.requests.get')
def test_tvmaze_get_show_details(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": 1, "name": "Test Show Details"}
    mock_get.return_value = mock_response

    result = tvmaze_client.get_show_details(1)
    assert result['name'] == "Test Show Details"

@patch('api_clients.tvmaze_client.requests.get')
def test_tvmaze_get_show_episodes(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"id": 101, "name": "Pilot"}]
    mock_get.return_value = mock_response

    result = tvmaze_client.get_show_episodes(1)
    assert len(result) == 1
    assert result[0]['name'] == "Pilot"

# --- Yahoo Finance Tests ---

@patch('api_clients.yahoo_finance_client.yf.Ticker')
def test_yahoo_get_stock_price_success(mock_ticker_cls):
    # Mock the Ticker instance and its history/info
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    
    # Mock history dataframe
    # We need to mock pandas DataFrame behavior slightly for .empty and .iloc
    # But since the code uses .empty, .iloc[-1], we can mock those attributes on the return value
    
    mock_hist = MagicMock()
    mock_hist.empty = False
    
    # Mocking row access. simpler to just mock what .iloc[-1] returns (a dict-like object)
    mock_latest_data = {
        'Close': 150.00,
        'High': 155.00,
        'Low': 148.00,
        'Volume': 1000000
    }
    mock_prev_data = {
        'Close': 145.00
    }
    
    # Setup iloc to support [-1] and [-2]
    def iloc_side_effect(idx):
        if idx == -1:
            return mock_latest_data
        if idx == -2:
            return mock_prev_data
        return None
        
    mock_hist.iloc.__getitem__.side_effect = iloc_side_effect
    mock_hist.__len__.return_value = 2
    
    # Mock index for date
    mock_date = MagicMock()
    mock_date.strftime.return_value = "2023-01-01"
    mock_hist.index = [MagicMock(), mock_date] # last one is mock_date
    
    mock_ticker.history.return_value = mock_hist
    
    # Mock info
    mock_ticker.info = {
        'currency': 'USD',
        'exchange': 'NMS',
        'longName': 'Test Corp'
    }

    result = yahoo_finance_client.get_stock_price("TEST")
    
    assert result is not None
    assert result['01. symbol'] == "TEST"
    assert result['05. price'] == "150.00"
    assert result['longName'] == "Test Corp"

@patch('api_clients.yahoo_finance_client.yf.Ticker')
def test_yahoo_get_stock_news(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    
    mock_ticker.news = [
        {
            "title": "News Title",
            "link": "http://news.com",
            "publisher": "NewsSource",
            "providerPublishTime": 1672531200
        }
    ]
    
    result = yahoo_finance_client.get_stock_news("TEST")
    assert len(result) == 1
    assert result[0]['title'] == "News Title"

# --- OpenWeatherMap Tests ---

class MockResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status = status
    async def json(self):
        return self._data
    async def text(self):
        return str(self._data)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass

@pytest.mark.asyncio
async def test_get_weather_data_success():
    # Mock aiohttp session
    mock_session = MagicMock()
    
    # Data
    current_data = {
        "name": "London",
        "sys": {"country": "GB", "sunrise": 100, "sunset": 200},
        "main": {"temp": 15.0, "feels_like": 14.0, "humidity": 80, "pressure": 1012},
        "weather": [{"main": "Clouds", "description": "overcast clouds", "icon": "04d"}],
        "wind": {"speed": 5.0, "deg": 180},
        "clouds": {"all": 90},
        "timezone": 0,
        "cod": 200
    }
    
    forecast_data = {
        "list": [
            {
                "dt": 1672531200,
                "main": {"temp": 14.0},
                "weather": [{"main": "Rain", "description": "light rain", "icon": "10d"}]
            }
        ]
    }

    # side_effect for session.get call
    def get_side_effect(url, params=None):
        if "/weather" in url:
            return MockResponse(current_data)
        elif "/forecast" in url:
            return MockResponse(forecast_data)
        return MockResponse({}, status=404)

    mock_session.get.side_effect = get_side_effect
    
    # Let's patch the config variable in the openweathermap_client module
    with patch('api_clients.openweathermap_client.OPENWEATHERMAP_API_KEY', 'test_key'):
        result = await openweathermap_client.get_weather_data("London", mock_session)

    assert result is not None
    assert result["current"]["location_name"] == "London"
    assert result["current"]["temp"] == 15.0
    assert len(result["forecast"]) == 1
    assert result["forecast"][0]["condition"] == "Rain"

@pytest.mark.asyncio
async def test_get_weather_data_missing_key():
    with patch('api_clients.openweathermap_client.OPENWEATHERMAP_API_KEY', None):
        result = await openweathermap_client.get_weather_data("London", MagicMock())
        assert result is None

