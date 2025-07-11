# api_clients/yahoo_finance_client.py

import yfinance as yf
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any
import requests  # Added for direct Yahoo API calls
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Polish stock symbols that should use .WA suffix
POLISH_STOCK_SYMBOLS = {
    'LPP', 'PKN', 'CDR', 'PKO', 'PEO', 'JSW', 'LTS', 'DNP', 'CPS', 'SPL',
    'ACP', 'ALR', 'AMC', 'ASB', 'ATT', 'BDX', 'BFT', 'BNP', 'BOS', 'BRA',
    'OPL', 'PGE', 'PZU', 'TEN', 'MIL', 'KGH', 'KTY', 'MBK', 'CCC', 'LWB',
    'GTC', 'PBX', 'XTB', 'EAT', 'EUR', 'ALE', 'DVL', 'COD', 'INR', 'KRU',
    # Additional Polish stocks from user's comprehensive list
    'ING', 'BHW', 'TPE', 'ENA', 'CAR', 'DOM', 'PEP', 'ENG', 'GPP', 'NWG', 
    'ASE', 'NEU', 'ABS', 'WPL', '1AT', 'APR', 'VRC', 'ARH', 'CBF', 'RBW',
    'SGN', 'GPW', 'PLW', 'MLG', 'ECH', 'SNT', 'ABE', 'GEA', 'MRB', 'VOX',
    'MUR', 'TAR', 'TXT', 'PCR', 'MNC', 'STP', 'COG', 'SHO', 'BRS', 'MCI',
    'ZEP', 'UNT', 'CLN', 'DBC', 'PCE', 'OPN', 'CMP', 'HUG', 'ZAP', 'TRK',
    'IMC', 'TOR', 'KGN', 'VRG', 'ATC', 'TME', '11B', 'BMC', 'BML', 'CIG',
    'CTX', 'DCR', 'ELZ', 'ERG', 'FTH', 'KER', 'KRK', 'LVC', 'MAB', 'MCR',
    'MEG', 'MRG', 'PBS', 'PCO', 'PGM', 'PUR', 'SFS', 'SNS', 'STX', 'SWG',
    'TMR', 'TSG', 'VKT'
}

# Other European stock exchanges
EUROPEAN_EXCHANGES = {
    '.L': 'London Stock Exchange',
    '.PA': 'Euronext Paris', 
    '.AS': 'Euronext Amsterdam',
    '.MI': 'Borsa Italiana Milan',
    '.F': 'Frankfurt Stock Exchange',
    '.WA': 'Warsaw Stock Exchange'
}

def normalize_symbol(symbol: str) -> str:
    """
    Normalize stock symbol for Yahoo Finance format.
    
    Args:
        symbol: Stock symbol (e.g., 'LPP', 'AAPL', 'LPP.WA')
    
    Returns:
        Normalized symbol for Yahoo Finance
    """
    symbol = symbol.upper().strip()
    
    # If already has exchange suffix, return as-is
    if any(symbol.endswith(suffix) for suffix in EUROPEAN_EXCHANGES.keys()):
        return symbol
    
    # Add .WA suffix for known Polish stocks
    if symbol in POLISH_STOCK_SYMBOLS:
        return f"{symbol}.WA"
    
    # Return as-is for US stocks and others
    return symbol

def get_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Fetch current quote data using Yahoo Finance Query1 API.
    """
    normalized = normalize_symbol(symbol)
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": normalized}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("quoteResponse", {}).get("result", [])
        if not results:
            logger.warning(f"No quote data in response for {normalized}")
            return None
        q = results[0]
        from datetime import datetime as _dt
        return {
            "01. symbol": normalized,
            "05. price": f"{q.get('regularMarketPrice', 0):.2f}",
            "09. change": f"{q.get('regularMarketChange', 0):+.2f}",
            "10. change percent": f"{q.get('regularMarketChangePercent', 0):+.2f}%", 
            "03. high": f"{q.get('regularMarketDayHigh', 0):.2f}",
            "04. low": f"{q.get('regularMarketDayLow', 0):.2f}",
            "06. volume": str(int(q.get('regularMarketVolume', 0))),
            "07. latest trading day": _dt.fromtimestamp(q.get("regularMarketTime", 0)).strftime('%Y-%m-%d'),
            "source": "yahoo_finance",
            "currency": q.get('currency', ''),
            "exchange": q.get('fullExchangeName', '')
        }
    except Exception as e:
        logger.error(f"Error in direct get_quote for {normalized}: {e}")
        return None

def get_stock_price(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Get current stock price, first via direct quote API, then fallback to yfinance.
    """
    # Try direct quote API first
    quote = get_quote(symbol)
    if quote:
        return quote

    # Fallback to yfinance Ticker history method
    try:
        normalized_symbol = normalize_symbol(symbol)
        logger.info(f"Fetching Yahoo Finance data via yfinance for {normalized_symbol}")
        
        ticker = yf.Ticker(normalized_symbol)
        hist = ticker.history(period="5d")
        
        if hist.empty:
            logger.warning(f"No historical data found for {normalized_symbol}")
            return None
            
        latest_data = hist.iloc[-1]
        info = {}
        try:
            info = ticker.info
        except Exception as e:
            logger.warning(f"Could not fetch info for {normalized_symbol}: {e}")
            info = {}
            
        change, change_percent = 0.0, 0.0
        if len(hist) >= 2:
            prev_close = hist.iloc[-2]['Close']
            change = latest_data['Close'] - prev_close
            change_percent = (change / prev_close) * 100
            
        result = {
            '01. symbol': normalized_symbol,
            '05. price': f"{latest_data['Close']:.2f}",
            '09. change': f"{change:+.2f}",
            '10. change percent': f"{change_percent:+.2f}%",
            '03. high': f"{latest_data['High']:.2f}",
            '04. low': f"{latest_data['Low']:.2f}",
            '06. volume': str(int(latest_data['Volume'])),
            '07. latest trading day': hist.index[-1].strftime('%Y-%m-%d'),
            'source': 'yahoo_finance',
            'currency': info.get('currency', 'USD'),
            'exchange': info.get('exchange', 'Unknown')
        }
        return result
        
    except Exception as e:
        logger.error(f"Error fetching Yahoo Finance data via fallback for {symbol}: {e}")
        return None

def get_daily_time_series(symbol: str, outputsize: str = "compact") -> Optional[List[Tuple[str, float]]]:
    """
    Get daily time series data for charts.
    
    Args:
        symbol: Stock symbol
        outputsize: 'compact' for last 100 days, 'full' for all available
    
    Returns:
        List of (date_string, close_price) tuples or None if error
    """
    try:
        normalized_symbol = normalize_symbol(symbol)
        logger.info(f"Fetching Yahoo Finance daily time series for {normalized_symbol}")
        
        ticker = yf.Ticker(normalized_symbol)
        
        # Determine period based on outputsize
        period = "3mo" if outputsize == "compact" else "max"
        hist = ticker.history(period=period)
        
        if hist.empty:
            logger.warning(f"No historical data found for {normalized_symbol}")
            return None
        
        # Convert to format expected by chart generation
        time_series_data = []
        for date, row in hist.iterrows():
            date_str = date.strftime('%Y-%m-%d')
            close_price = float(row['Close'])
            time_series_data.append((date_str, close_price))
        
        # Sort chronologically (oldest first)
        time_series_data.sort(key=lambda x: x[0])
        
        logger.info(f"Successfully fetched {len(time_series_data)} data points for {normalized_symbol}")
        return time_series_data
        
    except Exception as e:
        logger.error(f"Error fetching Yahoo Finance time series for {symbol}: {e}")
        return None

def get_intraday_time_series(symbol: str, interval: str = "60min", outputsize: str = "compact") -> Optional[List[Tuple[str, float]]]:
    """
    Get intraday time series data.
    
    Args:
        symbol: Stock symbol
        interval: Data interval (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo)
        outputsize: Not used for Yahoo Finance, kept for compatibility
    
    Returns:
        List of (datetime_string, close_price) tuples or None if error
    """
    try:
        normalized_symbol = normalize_symbol(symbol)
        logger.info(f"Fetching Yahoo Finance intraday data for {normalized_symbol}, interval: {interval}")
        
        ticker = yf.Ticker(normalized_symbol)
        
        # Map interval to Yahoo Finance format and determine period
        if interval in ["1min", "1m"]:
            yf_interval = "1m"
            period = "1d"
        elif interval in ["5min", "5m"]:
            yf_interval = "5m" 
            period = "5d"
        elif interval in ["15min", "15m"]:
            yf_interval = "15m"
            period = "5d"
        elif interval in ["30min", "30m"]:
            yf_interval = "30m"
            period = "5d"
        elif interval in ["60min", "60m", "1h"]:
            yf_interval = "1h"
            period = "1mo"
        else:
            yf_interval = "1h"
            period = "1mo"
        
        hist = ticker.history(period=period, interval=yf_interval)
        
        if hist.empty:
            logger.warning(f"No intraday data found for {normalized_symbol}")
            return None
        
        # Convert to format expected by chart generation
        time_series_data = []
        for date, row in hist.iterrows():
            # Format datetime for intraday data
            datetime_str = date.strftime('%Y-%m-%d %H:%M:%S')
            close_price = float(row['Close'])
            time_series_data.append((datetime_str, close_price))
        
        # Sort chronologically
        time_series_data.sort(key=lambda x: x[0])
        
        logger.info(f"Successfully fetched {len(time_series_data)} intraday data points for {normalized_symbol}")
        return time_series_data
        
    except Exception as e:
        logger.error(f"Error fetching Yahoo Finance intraday data for {symbol}: {e}")
        return None

def search_symbol(query: str) -> List[Dict[str, str]]:
    """
    Search for stock symbols (basic implementation).
    Yahoo Finance doesn't have a direct search API, so this is a placeholder.
    
    Args:
        query: Search query
    
    Returns:
        List of symbol matches (limited functionality)
    """
    # This is a basic implementation - Yahoo Finance doesn't have a search API
    # In a full implementation, you might use other services or symbol lists
    
    query_upper = query.upper()
    
    # Check if it's a known Polish stock
    if query_upper in POLISH_STOCK_SYMBOLS:
        return [{
            'symbol': f"{query_upper}.WA",
            'name': f"{query_upper} (Warsaw Stock Exchange)",
            'type': 'stock',
            'exchange': 'Warsaw Stock Exchange'
        }]
    
    # Basic symbol validation
    if len(query_upper) <= 5 and query_upper.isalpha():
        return [{
            'symbol': query_upper,
            'name': f"{query_upper} (Symbol)",
            'type': 'stock',
            'exchange': 'Unknown'
        }]
    
    return []

def test_connection() -> bool:
    """
    Test Yahoo Finance connection with a simple request.
    
    Returns:
        True if connection successful, False otherwise
    """
    try:
        # Test with a well-known stock
        ticker = yf.Ticker("AAPL")
        hist = ticker.history(period="1d")
        return not hist.empty
    except Exception as e:
        logger.error(f"Yahoo Finance connection test failed: {e}")
        return False

if __name__ == "__main__":
    # Test the implementation
    print("Testing Yahoo Finance client...")
    
    # Test Polish stock
    print("\n1. Testing LPP.WA (Polish stock):")
    lpp_data = get_stock_price("LPP")
    if lpp_data:
        print(f"LPP Price: {lpp_data['05. price']} {lpp_data.get('currency', 'PLN')}")
        print(f"Change: {lpp_data['09. change']} ({lpp_data['10. change percent']})")
        print(f"Exchange: {lpp_data.get('exchange', 'Unknown')}")
    else:
        print("Failed to fetch LPP data")
    
    # Test US stock
    print("\n2. Testing AAPL (US stock):")
    aapl_data = get_stock_price("AAPL")
    if aapl_data:
        print(f"AAPL Price: {aapl_data['05. price']} {aapl_data.get('currency', 'USD')}")
        print(f"Change: {aapl_data['09. change']} ({aapl_data['10. change percent']})")
    else:
        print("Failed to fetch AAPL data")
    
    # Test time series
    print("\n3. Testing time series for LPP:")
    lpp_series = get_daily_time_series("LPP", "compact")
    if lpp_series:
        print(f"Fetched {len(lpp_series)} data points")
        print(f"Latest: {lpp_series[-1]}")
    else:
        print("Failed to fetch LPP time series")
    
    print("\nYahoo Finance client test completed!") 