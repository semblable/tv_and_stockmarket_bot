# api_clients/alpha_vantage_client.py

import requests
import config # To access ALPHA_VANTAGE_API_KEY

ALPHA_VANTAGE_API_KEY = config.ALPHA_VANTAGE_API_KEY
BASE_URL = "https://www.alphavantage.co/query"

def get_stock_price(symbol: str):
    """
    Fetches the current stock price and other quote data for a given symbol using Alpha Vantage.

    Args:
        symbol: The stock symbol (e.g., "IBM").

    Returns:
        A dictionary containing the stock quote if successful.
        This dictionary includes keys like '05. price' for the current price
        and '08. previous close' for the previous day's closing price.
        A dictionary with an "error" key if an API limit is reached.
        None if an error occurs (e.g., invalid symbol, network issue).
    """
    if not ALPHA_VANTAGE_API_KEY:
        # Consider logging this instead of printing, or raise an exception
        # if the API key is essential for the application's core functionality.
        print("CRITICAL: ALPHA_VANTAGE_API_KEY not configured.")
        return {"error": "config_error", "message": "ALPHA_VANTAGE_API_KEY not configured."}

    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
        "apikey": ALPHA_VANTAGE_API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=10) # Added timeout
        response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)
        data = response.json()

        if "Global Quote" in data and data["Global Quote"] and isinstance(data["Global Quote"], dict) and data["Global Quote"].get("01. symbol"):
            # Check if "Global Quote" is not empty and is a dictionary with actual data
            return data["Global Quote"]
        elif "Error Message" in data:
            # This indicates an API-level error, like an invalid symbol
            error_message = data['Error Message']
            print(f"Alpha Vantage API Error for '{symbol}': {error_message}")
            return {"error": "api_error", "message": error_message}
        elif "Note" in data:
            # This often indicates an API call frequency limit
            print(f"Alpha Vantage API Note for '{symbol}': {data['Note']}")
            return {"error": "api_limit", "message": data['Note']}
        elif not data: # Handles empty response
            print(f"Empty response from Alpha Vantage for '{symbol}'.")
            return None
        else:
            # Unexpected response structure
            print(f"Unexpected response structure from Alpha Vantage for '{symbol}': {data}")
            return None

    except requests.exceptions.Timeout:
        print(f"Timeout error fetching stock price for '{symbol}' from Alpha Vantage.")
        return None
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error fetching stock price for '{symbol}' from Alpha Vantage: {http_err} - Response: {response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        # Catching other general request exceptions (network, connection, etc.)
        print(f"Request error fetching stock price for '{symbol}' from Alpha Vantage: {req_err}")
        return None
    except ValueError:  # Includes JSONDecodeError
        print(f"Error decoding JSON response from Alpha Vantage for '{symbol}'. Response: {response.text if 'response' in locals() else 'N/A'}")
        return None


def get_stock_news(symbol: str, limit: int = 5):
    """
    Fetches recent news articles for a given stock symbol using Alpha Vantage.

    Args:
        symbol: The stock symbol (e.g., "IBM").
        limit: The maximum number of news articles to return.

    Returns:
        A list of dictionaries, where each dictionary represents a news article,
        if successful.
        A dictionary with an "error" key if an API limit is reached or config error.
        None if an error occurs (e.g., invalid symbol, network issue, no news found).
    """
    if not ALPHA_VANTAGE_API_KEY:
        print("CRITICAL: ALPHA_VANTAGE_API_KEY not configured.")
        return {"error": "config_error", "message": "ALPHA_VANTAGE_API_KEY not configured."}

    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": symbol, # Note: API uses 'tickers' for this endpoint
        "apikey": ALPHA_VANTAGE_API_KEY,
        "limit": limit # Alpha Vantage API supports a limit parameter, default is 50
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=15) # Increased timeout for potentially larger response
        response.raise_for_status()
        data = response.json()

        if "feed" in data and isinstance(data["feed"], list):
            articles = []
            for item in data["feed"][:limit]: # Ensure we respect the local limit as well
                # Parse time_published (e.g., "20231026T103000")
                time_published_str = item.get("time_published", "")
                parsed_time = ""
                if len(time_published_str) == 15: # YYYYMMDDTHHMMSS
                    try:
                        parsed_time = f"{time_published_str[0:4]}-{time_published_str[4:6]}-{time_published_str[6:8]} {time_published_str[9:11]}:{time_published_str[11:13]}:{time_published_str[13:15]}"
                    except ValueError:
                        parsed_time = time_published_str # Fallback to raw string

                articles.append({
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "source": item.get("source"),
                    "time_published": parsed_time,
                    "summary": item.get("summary"),
                    "sentiment_label": item.get("overall_sentiment_label", "N/A"),
                    "sentiment_score": item.get("overall_sentiment_score", "N/A")
                })
            return articles if articles else None # Return None if no articles processed
        elif "Error Message" in data:
            error_message = data['Error Message']
            print(f"Alpha Vantage API Error for news on '{symbol}': {error_message}")
            return {"error": "api_error", "message": error_message}
        elif "Information" in data or "Note" in data: # "Information" can also indicate API limits/issues
            message = data.get("Information", data.get("Note", "API usage limit or issue."))
            print(f"Alpha Vantage API Info/Note for news on '{symbol}': {message}")
            return {"error": "api_limit", "message": message}
        elif not data or ("feed" in data and not data["feed"]): # Handles empty response or empty feed
            print(f"No news found or empty response from Alpha Vantage for '{symbol}'.")
            return None # No news is not an error, but no data to return
        else:
            print(f"Unexpected response structure for news from Alpha Vantage for '{symbol}': {data}")
            return None

    except requests.exceptions.Timeout:
        print(f"Timeout error fetching stock news for '{symbol}' from Alpha Vantage.")
        return None
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error fetching stock news for '{symbol}' from Alpha Vantage: {http_err} - Response: {response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        print(f"Request error fetching stock news for '{symbol}' from Alpha Vantage: {req_err}")
        return None
    except ValueError:  # Includes JSONDecodeError
        print(f"Error decoding JSON response for news from Alpha Vantage for '{symbol}'. Response: {response.text if 'response' in locals() else 'N/A'}")
        return None


def get_daily_time_series(symbol: str, outputsize: str = 'compact'):
    """
    Fetches daily time series data (date, open, high, low, close, volume) for a stock.

    Args:
        symbol: The stock symbol (e.g., "IBM").
        outputsize: 'compact' for last 100 data points, 'full' for full-length series.

    Returns:
        A list of tuples (date_str, close_price_float), sorted by date ascending.
        Returns a dict with "error" key on API limit or config error.
        Returns None on other errors or if no data.
    """
    if not ALPHA_VANTAGE_API_KEY:
        print("CRITICAL: ALPHA_VANTAGE_API_KEY not configured.")
        return {"error": "config_error", "message": "ALPHA_VANTAGE_API_KEY not configured."}

    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": symbol,
        "outputsize": outputsize,
        "apikey": ALPHA_VANTAGE_API_KEY,
        "datatype": "json" # Ensure JSON response
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        if "Time Series (Daily)" in data:
            time_series = data["Time Series (Daily)"]
            # Convert to list of (date, close_price) tuples
            # Alpha Vantage returns data with newest date first, so we reverse for charting
            chart_data = []
            for date_str, daily_data in time_series.items():
                try:
                    # Ensure '4. close' exists and is a valid number
                    close_price = float(daily_data["4. close"])
                    chart_data.append((date_str, close_price))
                except (KeyError, ValueError) as e:
                    print(f"Warning: Skipping data point for {symbol} on {date_str} due to missing/invalid close price: {e}")
                    continue # Skip this data point

            if not chart_data: # If all points were skipped or original data was empty in structure
                print(f"No valid daily time series data points found for '{symbol}' after parsing.")
                return None

            chart_data.sort(key=lambda x: x[0]) # Sort by date ascending
            return chart_data
        elif "Error Message" in data:
            error_message = data['Error Message']
            print(f"Alpha Vantage API Error for daily series of '{symbol}': {error_message}")
            return {"error": "api_error", "message": error_message}
        elif "Note" in data or "Information" in data:
            message = data.get("Note", data.get("Information", "API usage limit or issue."))
            print(f"Alpha Vantage API Note/Info for daily series of '{symbol}': {message}")
            return {"error": "api_limit", "message": message}
        else:
            print(f"Unexpected response structure for daily series from Alpha Vantage for '{symbol}': {data}")
            return None

    except requests.exceptions.Timeout:
        print(f"Timeout error fetching daily time series for '{symbol}' from Alpha Vantage.")
        return None
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error fetching daily time series for '{symbol}' from Alpha Vantage: {http_err} - Response: {response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        print(f"Request error fetching daily time series for '{symbol}' from Alpha Vantage: {req_err}")
        return None
    except ValueError:  # Includes JSONDecodeError
        print(f"Error decoding JSON response for daily time series from Alpha Vantage for '{symbol}'. Response: {response.text if 'response' in locals() else 'N/A'}")
        return None


def get_intraday_time_series(symbol: str, interval: str = '60min', outputsize: str = 'compact'):
    """
    Fetches intraday time series data for a stock.

    Args:
        symbol: The stock symbol (e.g., "IBM").
        interval: Time interval between two consecutive data points.
                  Supported: '1min', '5min', '15min', '30min', '60min'.
        outputsize: 'compact' for last 100 data points, 'full' for full-length series.
                    Note: Intraday 'full' can be very large (many months of 1min data).

    Returns:
        A list of tuples (datetime_str, close_price_float), sorted by datetime ascending.
        Returns a dict with "error" key on API limit or config error.
        Returns None on other errors or if no data.
    """
    if not ALPHA_VANTAGE_API_KEY:
        print("CRITICAL: ALPHA_VANTAGE_API_KEY not configured.")
        return {"error": "config_error", "message": "ALPHA_VANTAGE_API_KEY not configured."}

    # Validate interval
    supported_intervals = ['1min', '5min', '15min', '30min', '60min']
    if interval not in supported_intervals:
        print(f"Unsupported interval '{interval}' for intraday time series. Supported: {supported_intervals}")
        # Or raise ValueError("Invalid interval")
        return {"error": "param_error", "message": f"Invalid interval: {interval}. Supported: {supported_intervals}"}


    params = {
        "function": "TIME_SERIES_INTRADAY",
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": ALPHA_VANTAGE_API_KEY,
        "datatype": "json" # Ensure JSON response
    }
    # For intraday, Alpha Vantage premium is required for extended history (outputsize=full for more than a few days)
    # Free tier typically provides 1-5 days of intraday data for 'compact' and 'full'.

    try:
        response = requests.get(BASE_URL, params=params, timeout=20) # Potentially larger data
        response.raise_for_status()
        data = response.json()

        time_series_key = f"Time Series ({interval})"
        if time_series_key in data:
            time_series = data[time_series_key]
            chart_data = []
            for datetime_str, intraday_data in time_series.items():
                try:
                    close_price = float(intraday_data["4. close"])
                    chart_data.append((datetime_str, close_price))
                except (KeyError, ValueError) as e:
                    print(f"Warning: Skipping data point for {symbol} at {datetime_str} due to missing/invalid close price: {e}")
                    continue

            if not chart_data:
                print(f"No valid intraday time series data points found for '{symbol}' (interval: {interval}) after parsing.")
                return None

            chart_data.sort(key=lambda x: x[0]) # Sort by datetime ascending
            return chart_data
        elif "Error Message" in data:
            error_message = data['Error Message']
            print(f"Alpha Vantage API Error for intraday series of '{symbol}' (interval: {interval}): {error_message}")
            return {"error": "api_error", "message": error_message}
        elif "Note" in data or "Information" in data:
            message = data.get("Note", data.get("Information", "API usage limit or issue."))
            print(f"Alpha Vantage API Note/Info for intraday series of '{symbol}' (interval: {interval}): {message}")
            return {"error": "api_limit", "message": message}
        else:
            print(f"Unexpected response structure for intraday series from Alpha Vantage for '{symbol}' (interval: {interval}): {data}")
            return None

    except requests.exceptions.Timeout:
        print(f"Timeout error fetching intraday time series for '{symbol}' (interval: {interval}) from Alpha Vantage.")
        return None
    except requests.exceptions.HTTPError as http_err:
        print(f"HTTP error fetching intraday time series for '{symbol}' (interval: {interval}) from Alpha Vantage: {http_err} - Response: {response.text}")
        return None
    except requests.exceptions.RequestException as req_err:
        print(f"Request error fetching intraday time series for '{symbol}' (interval: {interval}) from Alpha Vantage: {req_err}")
        return None
    except ValueError:  # Includes JSONDecodeError
        print(f"Error decoding JSON response for intraday time series from Alpha Vantage for '{symbol}' (interval: {interval}). Response: {response.text if 'response' in locals() else 'N/A'}")
        return None

def get_currency_exchange_rate(from_currency: str, to_currency: str):
    """
    Fetches the real-time exchange rate for a currency pair.

    Args:
        from_currency: The currency to convert from (e.g., "USD", "EUR").
        to_currency: The currency to convert to (e.g., "PLN", "USD").

    Returns:
        A float representing the exchange rate if successful.
        Returns None on error.
    """
    if not ALPHA_VANTAGE_API_KEY:
        print("CRITICAL: ALPHA_VANTAGE_API_KEY not configured.")
        return None

    params = {
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": from_currency,
        "to_currency": to_currency,
        "apikey": ALPHA_VANTAGE_API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "Realtime Currency Exchange Rate" in data:
            rate_str = data["Realtime Currency Exchange Rate"].get("5. Exchange Rate")
            if rate_str:
                return float(rate_str)
        elif "Error Message" in data:
            print(f"Alpha Vantage API Error for exchange rate {from_currency}/{to_currency}: {data['Error Message']}")
        elif "Note" in data:
            print(f"Alpha Vantage API Note for exchange rate {from_currency}/{to_currency}: {data['Note']}")
        
        return None

    except Exception as e:
        print(f"Error fetching exchange rate for {from_currency}/{to_currency}: {e}")
        return None

def search_symbol(keywords: str):
    """
    Searches for stock symbols matching the keywords.

    Args:
        keywords: The search keywords (e.g., "Microsoft", "BA").

    Returns:
        A list of dictionaries representing the search results (symbol, name, type, region, etc.).
        Returns None on error or no results.
    """
    if not ALPHA_VANTAGE_API_KEY:
        print("CRITICAL: ALPHA_VANTAGE_API_KEY not configured.")
        return []

    params = {
        "function": "SYMBOL_SEARCH",
        "keywords": keywords,
        "apikey": ALPHA_VANTAGE_API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "bestMatches" in data:
            return data["bestMatches"]
        elif "Note" in data or "Information" in data:
            # Limit reached
            print(f"Alpha Vantage API Note/Info for symbol search '{keywords}': {data.get('Note', data.get('Information'))}")
            return []
        else:
            return []

    except Exception as e:
        print(f"Error searching for symbol '{keywords}': {e}")
        return []


if __name__ == '__main__':
    print("\n--- Alpha Vantage Client Test ---")

    if not ALPHA_VANTAGE_API_KEY or ALPHA_VANTAGE_API_KEY == "YOUR_API_KEY_HERE": # Added check for placeholder
        print("CRITICAL: ALPHA_VANTAGE_API_KEY is not set or is a placeholder.")
        print("Please set it in config.py or your .env file to run tests.")
        print("--- End of Alpha Vantage Client Test (Skipped) ---\n")
    else:
        print(f"Using API Key: ...{ALPHA_VANTAGE_API_KEY[-4:]}") # Show only last 4 chars for security

        # --- get_stock_price Tests ---
        print("\n--- Testing get_stock_price ---")
        # Test Case 1: Valid Symbol for Price
        print("\n--- Test Case 1.1: Valid Symbol Price (IBM) ---")
        valid_symbol_price = "IBM"
        price_data_valid = get_stock_price(valid_symbol_price)
        if price_data_valid:
            if price_data_valid.get("error") == "api_limit":
                print(f"API Limit Reached for {valid_symbol_price}: {price_data_valid.get('message')}")
            elif "05. price" in price_data_valid and "08. previous close" in price_data_valid:
                print(f"Successfully fetched price data for {price_data_valid.get('01. symbol', valid_symbol_price)}:")
                print(f"  Current Price: {price_data_valid['05. price']}")
                print(f"  Previous Close: {price_data_valid['08. previous close']}")
            else:
                print(f"Received price data for {valid_symbol_price}, but key '05. price' or '08. previous close' is missing or data is malformed: {price_data_valid}")
        else:
            print(f"Failed to retrieve price data for {valid_symbol_price}.")

        # Test Case 2: Invalid Symbol for Price
        print("\n--- Test Case 1.2: Invalid Symbol Price (INVALIDSTOCK) ---")
        invalid_symbol_price = "INVALIDSTOCKXYZ123"
        price_data_invalid = get_stock_price(invalid_symbol_price)
        if price_data_invalid is None or price_data_invalid.get("error") == "api_error": # API might return error dict
            print(f"Correctly handled invalid symbol '{invalid_symbol_price}' for price. Response: {price_data_invalid}")
        elif price_data_invalid.get("error") == "api_limit":
             print(f"API Limit Reached for {invalid_symbol_price}: {price_data_invalid.get('message')}")
        else:
            print(f"Unexpected response for invalid symbol price '{invalid_symbol_price}': {price_data_invalid}")

        # --- get_stock_news Tests ---
        print("\n\n--- Testing get_stock_news ---")
        # Test Case 2.1: Valid Symbol for News
        print("\n--- Test Case 2.1: Valid Symbol News (AAPL, limit 3) ---")
        valid_symbol_news = "AAPL"
        news_data_valid = get_stock_news(valid_symbol_news, limit=3)
        if news_data_valid:
            if isinstance(news_data_valid, dict) and news_data_valid.get("error") == "api_limit":
                print(f"API Limit Reached for news on {valid_symbol_news}: {news_data_valid.get('message')}")
            elif isinstance(news_data_valid, list):
                print(f"Successfully fetched {len(news_data_valid)} news articles for {valid_symbol_news}:")
                for i, article in enumerate(news_data_valid):
                    print(f"  Article {i+1}:")
                    print(f"    Title: {article.get('title')}")
                    print(f"    Source: {article.get('source')}")
                    print(f"    Published: {article.get('time_published')}")
                    print(f"    Sentiment: {article.get('sentiment_label')} ({article.get('sentiment_score')})")
                    print(f"    URL: {article.get('url')}")
                    print(f"    Summary: {article.get('summary', 'N/A')[:100]}...") # Print first 100 chars of summary
            else: # Should be None if no news or other non-error dict issue
                 print(f"Received unexpected data for news on {valid_symbol_news}: {news_data_valid}")
        elif news_data_valid is None:
             print(f"No news found for {valid_symbol_news}, or an issue occurred that wasn't an API limit/error dict.")
        else: # Should not happen if logic is correct (should be list, dict with error, or None)
            print(f"Failed to retrieve news data for {valid_symbol_news} with unexpected return: {news_data_valid}")


        # Test Case 2.2: Symbol with Potentially No News
        print("\n--- Test Case 2.2: Symbol with Potentially No News (XYZNONEXISTENT) ---")
        no_news_symbol = "XYZNONEXISTENT" # A symbol unlikely to have news
        news_data_none = get_stock_news(no_news_symbol)
        if news_data_none is None:
            print(f"Correctly handled '{no_news_symbol}', no news found or API indicated no data (returned None).")
        elif isinstance(news_data_none, dict) and news_data_none.get("error") == "api_limit":
            print(f"API Limit Reached for news on {no_news_symbol}: {news_data_none.get('message')}")
        elif isinstance(news_data_none, dict) and news_data_none.get("error") == "api_error":
            print(f"API error for news on {no_news_symbol}: {news_data_none.get('message')} (This might be expected for truly invalid symbols)")
        else:
            print(f"Unexpected response for '{no_news_symbol}' (expected None or API error dict): {news_data_none}")
            
        # --- search_symbol Tests ---
        print("\n\n--- Testing search_symbol ---")
        search_kw = "Microsoft"
        print(f"\n--- Test Case 3.1: Search for '{search_kw}' ---")
        search_results = search_symbol(search_kw)
        if search_results:
            print(f"Found {len(search_results)} matches for '{search_kw}':")
            for match in search_results[:3]:
                 print(f"  {match.get('1. symbol')} - {match.get('2. name')} ({match.get('4. region')})")
        else:
            print(f"No results found for '{search_kw}' or API limit.")

        # Test Case (Shared): API Limit (Conceptual)
        print("\n--- Test Case (Shared): API Limit Handling (Conceptual) ---")
        print("Both functions are designed to return a dictionary like:")
        print("  {'error': 'api_limit', 'message': 'API call frequency limit reached.'}")
        print("if Alpha Vantage returns a 'Note' or 'Information' indicating a limit.")
        print("This allows the calling code (e.g., a cog) to inform the user appropriately.")

        print("\n--- End of Alpha Vantage Client Test ---\n")
