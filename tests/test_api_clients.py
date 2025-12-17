# tests/test_api_clients.py
import pytest
from unittest.mock import patch, MagicMock
from api_clients import alpha_vantage_client
from api_clients import tmdb_client
from api_clients import steam_client

# --- Alpha Vantage Tests ---

@patch('api_clients.alpha_vantage_client.requests.get')
def test_get_stock_price_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "Global Quote": {
            "01. symbol": "IBM",
            "05. price": "120.00",
            "08. previous close": "118.00",
            "09. change": "2.00",
            "10. change percent": "1.69%"
        }
    }
    mock_get.return_value = mock_response
    
    result = alpha_vantage_client.get_stock_price("IBM")
    assert result is not None
    assert result['01. symbol'] == "IBM"
    assert result['05. price'] == "120.00"

@patch('api_clients.alpha_vantage_client.requests.get')
def test_get_stock_price_api_limit(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "Note": "Thank you for using Alpha Vantage! Our standard API call frequency is 5 calls per minute..."
    }
    mock_get.return_value = mock_response
    
    result = alpha_vantage_client.get_stock_price("IBM")
    assert result is not None
    assert result.get("error") == "api_limit"

# --- TMDB Tests ---

@patch('api_clients.tmdb_client.requests.get')
def test_search_movie_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {"id": 1, "title": "Inception", "release_date": "2010-07-15", "poster_path": "/path.jpg"}
        ]
    }
    mock_get.return_value = mock_response
    
    result = tmdb_client.search_movie("Inception")
    assert len(result) == 1
    assert result[0]['title'] == "Inception"
    assert result[0]['id'] == 1

@patch('api_clients.tmdb_client.requests.get')
def test_search_tv_shows_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [
            {"id": 100, "name": "Breaking Bad", "first_air_date": "2008-01-20", "poster_path": "/bb.jpg"}
        ]
    }
    mock_get.return_value = mock_response
    
    result = tmdb_client.search_tv_shows("Breaking Bad")
    assert len(result) == 1
    assert result[0]['name'] == "Breaking Bad"
    assert result[0]['id'] == 100


# --- Steam matching tests (no network) ---

def test_steam_title_match_score_handles_typos_and_punctuation():
    q = "kingdom come deliverence 2"
    c = "Kingdom Come: Deliverance II"
    assert steam_client.title_match_score(q, c) >= 80


def test_steam_pick_best_store_match_selects_best_candidate():
    results = [
        {"appid": 1, "name": "Random DLC Pack", "type": "dlc"},
        {"appid": 2, "name": "Kingdom Come: Deliverance II", "type": "game"},
        {"appid": 3, "name": "Kingdom Come: Deliverance", "type": "game"},
    ]
    best = steam_client.pick_best_store_match("kingdom come deliverence 2", results)
    assert best is not None
    assert best["appid"] == 2


