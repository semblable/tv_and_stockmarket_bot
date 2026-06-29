# api_clients/yahoo_finance_client.py

import yfinance as yf
import logging
from datetime import datetime, date, timezone
from typing import Optional, List, Tuple, Dict, Any
from utils.api_utils import resilient_get, ttl_cache

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
    '.DE': 'Xetra (Germany)',
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
    
    # Strip .US suffix for US stocks (Yahoo Finance defaults to US)
    if symbol.endswith('.US'):
        return symbol[:-3]
    
    # Return as-is for US stocks and others
    return symbol

@ttl_cache(seconds=60)
def get_stock_price(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Get current stock price using yfinance library.
    """
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
            'exchange': info.get('exchange', 'Unknown'),
            'longName': info.get('longName', ''),
            'marketCap': info.get('marketCap', 'N/A'),
            'trailingPE': info.get('trailingPE', 'N/A'),
            'epsTrailingTwelveMonths': info.get('trailingEps', 'N/A'),
            'fiftyTwoWeekHigh': info.get('fiftyTwoWeekHigh', 'N/A'),
            'fiftyTwoWeekLow': info.get('fiftyTwoWeekLow', 'N/A')
        }
        return result
        
    except Exception as e:
        logger.error(f"Error fetching Yahoo Finance data for {symbol}: {e}")
        return None

def get_stock_news(symbol: str, limit: int = 5) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch recent news for a stock symbol using yfinance.
    
    Args:
        symbol: Stock symbol
        limit: Max number of articles
        
    Returns:
        List of news dictionaries or None
    """
    try:
        normalized_symbol = normalize_symbol(symbol)
        ticker = yf.Ticker(normalized_symbol)
        news = ticker.news
        
        if not news:
            return None
            
        processed_news = []
        for item in news[:limit]:
            # Convert timestamp to readable string
            published = item.get('providerPublishTime', 0)
            published_str = datetime.fromtimestamp(published).strftime('%Y-%m-%d %H:%M:%S') if published else "N/A"
            
            processed_news.append({
                "title": item.get("title", "No Title"),
                "url": item.get("link", ""),
                "source": item.get("publisher", "Yahoo Finance"),
                "time_published": published_str,
                "summary": "No summary available" if not item.get("relatedTickers") else f"Related: {', '.join(item.get('relatedTickers'))}", # Yahoo news structure varies, relatedTickers is a proxy for relevance context
                "sentiment_label": "N/A", # Yahoo doesn't provide sentiment label
                "sentiment_score": "N/A"
            })
            
        return processed_news
    except Exception as e:
        logger.error(f"Error fetching news for {symbol}: {e}")
        return None


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort float conversion; returns None for missing/invalid values."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _coerce_date(value: Any) -> Optional[date]:
    """
    Normalize the many shapes yfinance returns dates in (datetime, date,
    pandas Timestamp, unix timestamp, ISO string) into a ``datetime.date``.
    """
    if value is None:
        return None
    # pandas Timestamp and datetime are both datetime subclasses.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s[:len("YYYY-MM-DDTHH:MM:SS")], fmt).date()
            except ValueError:
                continue
        return None
    # Fallback: objects exposing a callable .date() (defensive).
    date_attr = getattr(value, "date", None)
    if callable(date_attr):
        try:
            d = date_attr()
            return d if isinstance(d, date) else None
        except Exception:
            return None
    return None


def _format_date(value: Any) -> Optional[str]:
    d = _coerce_date(value)
    return d.strftime("%Y-%m-%d") if d else None


@ttl_cache(seconds=3600)
def get_earnings_info(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Return the next earnings date (and EPS estimate when available) for a symbol.

    Returns a dict with ``next_earnings_date`` (``YYYY-MM-DD``) on success, or
    None if no upcoming earnings date can be determined.
    """
    try:
        normalized_symbol = normalize_symbol(symbol)
        ticker = yf.Ticker(normalized_symbol)

        next_date: Any = None
        eps_estimate: Any = None

        # Preferred source: the calendar (a dict in current yfinance).
        try:
            cal = ticker.calendar
        except Exception as e:
            logger.warning(f"Could not fetch calendar for {normalized_symbol}: {e}")
            cal = None

        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)) and ed:
                next_date = ed[0]
            elif ed:
                next_date = ed
            eps_estimate = cal.get("Earnings Average")

        # Fallback: scan get_earnings_dates() for the nearest future date.
        if _coerce_date(next_date) is None:
            try:
                df = ticker.get_earnings_dates(limit=16)
            except Exception:
                df = None
            if df is not None and getattr(df, "empty", True) is False:
                today = datetime.now(timezone.utc).date()
                best_date = None
                best_idx = None
                best_row = None
                for idx, row in df.iterrows():
                    d = _coerce_date(idx)
                    if d and d >= today and (best_date is None or d < best_date):
                        best_date, best_idx, best_row = d, idx, row
                if best_idx is not None:
                    next_date = best_idx
                    if best_row is not None:
                        try:
                            eps_estimate = best_row.get("EPS Estimate")
                        except Exception:
                            eps_estimate = None

        date_str = _format_date(next_date)
        if not date_str:
            logger.info(f"No upcoming earnings date found for {normalized_symbol}.")
            return None

        info: Dict[str, Any] = {}
        try:
            info = ticker.info or {}
        except Exception:
            info = {}
        if not isinstance(info, dict):
            info = {}

        return {
            "symbol": normalized_symbol,
            "next_earnings_date": date_str,
            "eps_estimate": _safe_float(eps_estimate),
            "currency": info.get("currency", "USD"),
            "longName": info.get("longName", ""),
        }
    except Exception as e:
        logger.error(f"Error fetching earnings info for {symbol}: {e}")
        return None


@ttl_cache(seconds=3600)
def get_dividend_info(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Return dividend details (yield, last payout, ex-dividend date) for a symbol.

    Returns None only when no ticker info is available. A valid non-dividend
    stock returns a dict with ``pays_dividend`` set to False.
    """
    try:
        normalized_symbol = normalize_symbol(symbol)
        ticker = yf.Ticker(normalized_symbol)

        try:
            info = ticker.info or {}
        except Exception as e:
            logger.warning(f"Could not fetch info for {normalized_symbol}: {e}")
            info = {}
        if not isinstance(info, dict) or not info:
            return None

        dividend_rate = _safe_float(info.get("dividendRate"))
        dividend_yield = _safe_float(info.get("dividendYield"))
        payout_ratio = _safe_float(info.get("payoutRatio"))

        # yfinance reports dividendYield as a fraction (0.025) in most versions
        # but occasionally as a percent; normalize values < 1 to a percentage.
        # payoutRatio is always a fraction (and can legitimately exceed 1.0 for
        # companies paying out more than they earn), so always scale it by 100.
        def _yield_as_percent(v: Optional[float]) -> Optional[float]:
            if v is None:
                return None
            return v * 100 if v < 1 else v

        return {
            "symbol": normalized_symbol,
            "currency": info.get("currency", "USD"),
            "longName": info.get("longName", ""),
            "pays_dividend": bool(dividend_rate or dividend_yield),
            "dividend_rate": dividend_rate,
            "dividend_yield_pct": _yield_as_percent(dividend_yield),
            "ex_dividend_date": _format_date(info.get("exDividendDate")),
            "last_dividend_value": _safe_float(info.get("lastDividendValue")),
            "payout_ratio_pct": (payout_ratio * 100) if payout_ratio is not None else None,
        }
    except Exception as e:
        logger.error(f"Error fetching dividend info for {symbol}: {e}")
        return None


@ttl_cache(seconds=600)
def get_daily_time_series(symbol: str, outputsize: str = "compact", period: str = None) -> Optional[List[Tuple[str, float]]]:
    """
    Get daily time series data for charts.
    
    Args:
        symbol: Stock symbol
        outputsize: 'compact' for last 100 days, 'full' for all available (deprecated if period is provided)
        period: Optional period to fetch (e.g., "1mo", "1y", "max", "ytd"). Overrides outputsize.
    
    Returns:
        List of (date_string, close_price) tuples or None if error
    """
    try:
        normalized_symbol = normalize_symbol(symbol)
        logger.info(f"Fetching Yahoo Finance daily time series for {normalized_symbol}")
        
        ticker = yf.Ticker(normalized_symbol)
        
        # Determine period
        if period:
             target_period = period
        else:
             target_period = "3mo" if outputsize == "compact" else "max"
             
        hist = ticker.history(period=target_period)
        
        if hist.empty:
            logger.warning(f"No historical data found for {normalized_symbol} with period {target_period}")
            return None
        
        # Convert to format expected by chart generation
        time_series_data = []
        for date, row in hist.iterrows():
            date_str = date.strftime('%Y-%m-%d')
            close_price = float(row['Close'])
            time_series_data.append((date_str, close_price))
        
        # Sort chronologically (oldest first)
        time_series_data.sort(key=lambda x: x[0])
        
        logger.info(f"Successfully fetched {len(time_series_data)} data points for {normalized_symbol} (period={target_period})")
        return time_series_data
        
    except Exception as e:
        logger.error(f"Error fetching Yahoo Finance time series for {symbol}: {e}")
        return None

@ttl_cache(seconds=300)
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

@ttl_cache(seconds=600)
def search_symbol(query: str) -> List[Dict[str, str]]:
    """
    Search for stock symbols using Yahoo Finance Auto-Complete API.
    """
    url = "https://query2.finance.yahoo.com/v1/finance/search"
    params = {
        "q": query,
        "lang": "en-US",
        "region": "US",
        "quotesCount": 10,
        "newsCount": 0,
        "enableFuzzyQuery": False,
        "quotesQueryId": "tss_match_phrase_query"
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        resp = resilient_get(url, params=params, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        
        quotes = data.get("quotes", [])
        results = []
        
        for q in quotes:
            if not q.get('symbol'): continue
            results.append({
                'symbol': q['symbol'],
                'name': q.get('longname') or q.get('shortname') or q['symbol'],
                'type': q.get('quoteType', 'Unknown'),
                'exchange': q.get('exchange', 'Unknown'),
                'region': 'US' # Defaulting to US as region isn't always explicit in this specific endpoint, but mostly accurate
            })
            
        return results
        
    except Exception as e:
        logger.error(f"Error searching symbol {query}: {e}")
        # Fallback to basic check if API fails
        if query.upper() in POLISH_STOCK_SYMBOLS:
             return [{
                'symbol': f"{query.upper()}.WA",
                'name': f"{query.upper()} (Warsaw Stock Exchange)",
                'type': 'stock',
                'exchange': 'Warsaw Stock Exchange',
                 'region': 'PL'
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
        print(f"Market Cap: {aapl_data.get('marketCap')}")
        print(f"PE Ratio: {aapl_data.get('trailingPE')}")
    else:
        print("Failed to fetch AAPL data")
        
    # Test Search
    print("\n3. Testing Search for 'Microsoft':")
    results = search_symbol("Microsoft")
    for r in results[:3]:
        print(f"Found: {r['symbol']} - {r['name']}")

    print("\nYahoo Finance client test completed!")
