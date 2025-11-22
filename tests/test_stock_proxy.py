import pytest
from unittest.mock import patch, MagicMock
from stock_proxy_service import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_health_check(client):
    rv = client.get('/health')
    assert rv.status_code == 200
    assert rv.json['status'] == 'ok'

@patch('stock_proxy_service.get_stock_price')
def test_get_stock_data_success(mock_get_price, client):
    mock_get_price.return_value = {"01. symbol": "TEST", "05. price": "100.00"}
    rv = client.get('/stock/TEST')
    assert rv.status_code == 200
    assert rv.json['01. symbol'] == 'TEST'

@patch('stock_proxy_service.get_stock_price')
def test_get_stock_data_not_found(mock_get_price, client):
    mock_get_price.return_value = None
    rv = client.get('/stock/UNKNOWN')
    assert rv.status_code == 404

@patch('stock_proxy_service.get_daily_time_series')
def test_get_daily_data_success(mock_get_daily, client):
    mock_get_daily.return_value = [("2023-01-01", 100.0)]
    rv = client.get('/stock/TEST/daily?outputsize=compact')
    assert rv.status_code == 200
    assert len(rv.json['data']) == 1
    assert rv.json['data'][0][0] == "2023-01-01"

@patch('stock_proxy_service.get_intraday_time_series')
def test_get_intraday_data_success(mock_get_intraday, client):
    mock_get_intraday.return_value = [("2023-01-01 10:00:00", 100.0)]
    rv = client.get('/stock/TEST/intraday?interval=60min')
    assert rv.status_code == 200
    assert len(rv.json['data']) == 1
    assert rv.json['interval'] == '60min'

