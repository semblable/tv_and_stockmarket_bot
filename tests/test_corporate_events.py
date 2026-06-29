import re
from datetime import date
from unittest.mock import patch, MagicMock

from api_clients import yahoo_finance_client


# --- get_earnings_info ---

@patch('api_clients.yahoo_finance_client.yf.Ticker')
def test_get_earnings_info_from_calendar(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    mock_ticker.calendar = {
        "Earnings Date": [date(2026, 7, 30)],
        "Earnings Average": 2.45,
    }
    mock_ticker.info = {"currency": "USD", "longName": "Apple Inc."}

    result = yahoo_finance_client.get_earnings_info("AAPL")
    assert result is not None
    assert result["symbol"] == "AAPL"
    assert result["next_earnings_date"] == "2026-07-30"
    assert result["eps_estimate"] == 2.45
    assert result["longName"] == "Apple Inc."


@patch('api_clients.yahoo_finance_client.yf.Ticker')
def test_get_earnings_info_none_when_no_date(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    mock_ticker.calendar = {}
    mock_ticker.get_earnings_dates.return_value = MagicMock(empty=True)

    assert yahoo_finance_client.get_earnings_info("XYZ") is None


# --- get_dividend_info ---

@patch('api_clients.yahoo_finance_client.yf.Ticker')
def test_get_dividend_info_paying_stock(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    mock_ticker.info = {
        "currency": "USD",
        "longName": "Coca-Cola Company",
        "dividendRate": 1.94,
        "dividendYield": 0.031,        # fraction -> 3.1%
        "exDividendDate": 1782777600,  # unix timestamp
        "lastDividendValue": 0.485,
        "payoutRatio": 0.75,
    }

    result = yahoo_finance_client.get_dividend_info("KO")
    assert result is not None
    assert result["pays_dividend"] is True
    assert result["dividend_rate"] == 1.94
    assert abs(result["dividend_yield_pct"] - 3.1) < 1e-6
    assert result["payout_ratio_pct"] == 75.0
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", result["ex_dividend_date"])


@patch('api_clients.yahoo_finance_client.yf.Ticker')
def test_get_dividend_info_payout_ratio_above_100pct(mock_ticker_cls):
    # payoutRatio is a fraction and can exceed 1.0 (>100% payout); it must
    # always be scaled by 100, never passed through by the <1 heuristic.
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    mock_ticker.info = {
        "currency": "USD",
        "longName": "High Payout Inc.",
        "dividendRate": 2.0,
        "dividendYield": 0.05,
        "payoutRatio": 1.2,  # 120%
    }

    result = yahoo_finance_client.get_dividend_info("HIGH")
    assert result["payout_ratio_pct"] == 120.0


@patch('api_clients.yahoo_finance_client.yf.Ticker')
def test_get_dividend_info_non_paying_stock(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    mock_ticker.info = {
        "currency": "USD",
        "longName": "Tesla",
        "dividendRate": None,
        "dividendYield": None,
    }

    result = yahoo_finance_client.get_dividend_info("TSLA")
    assert result is not None
    assert result["pays_dividend"] is False


@patch('api_clients.yahoo_finance_client.yf.Ticker')
def test_get_dividend_info_no_data_returns_none(mock_ticker_cls):
    mock_ticker = MagicMock()
    mock_ticker_cls.return_value = mock_ticker
    mock_ticker.info = {}

    assert yahoo_finance_client.get_dividend_info("BADSYM") is None


# --- corporate event de-dup (StocksMixin) ---

def test_corporate_event_dedup(db_manager):
    uid = 12345
    assert db_manager.has_sent_corporate_event(uid, "AAPL", "earnings", "2026-07-30") is False

    assert db_manager.mark_corporate_event_sent(uid, "AAPL", "earnings", "2026-07-30") is True
    assert db_manager.has_sent_corporate_event(uid, "AAPL", "earnings", "2026-07-30") is True

    # Re-marking the same event is idempotent (ON CONFLICT DO NOTHING).
    assert db_manager.mark_corporate_event_sent(uid, "AAPL", "earnings", "2026-07-30") is True

    # A different date for the same symbol is not yet sent.
    assert db_manager.has_sent_corporate_event(uid, "AAPL", "earnings", "2026-10-30") is False

    # Symbol lookups are case-insensitive (stored upper-cased).
    assert db_manager.has_sent_corporate_event(uid, "aapl", "earnings", "2026-07-30") is True
