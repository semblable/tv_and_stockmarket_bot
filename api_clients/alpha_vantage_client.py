# api_clients/alpha_vantage_client.py

import requests
import config # To access ALPHA_VANTAGE_API_KEY

ALPHA_VANTAGE_API_KEY = config.ALPHA_VANTAGE_API_KEY
BASE_URL = "https://www.alphavantage.co/query"

def get_stock_price(symbol):
    """
    Fetches the current stock price for a given symbol using Alpha Vantage.
    """
    if not ALPHA_VANTAGE_API_KEY:
        print("Error: ALPHA_VANTAGE_API_KEY not configured.")
        return None
    
    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": symbol,
        "apikey": ALPHA_VANTAGE_API_KEY
    }
    
    try:
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)
        data = response.json()
        # Minimal error checking for Alpha Vantage response structure
        if "Global Quote" in data and data["Global Quote"]:
            return data["Global Quote"]
        elif "Error Message" in data:
            print(f"Alpha Vantage API Error for {symbol}: {data['Error Message']}")
            return None
        elif "Note" in data: # Handles API limit messages
             print(f"Alpha Vantage API Note for {symbol}: {data['Note']}")
             return {"symbol": symbol, "price": "N/A", "note": data['Note']} # Return a specific structure for API limit
        else:
            print(f"Unexpected response structure from Alpha Vantage for {symbol}: {data}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching stock price for {symbol} from Alpha Vantage: {e}")
        return None
    except ValueError: # Includes JSONDecodeError
        print(f"Error decoding JSON response from Alpha Vantage for {symbol}.")
        return None


if __name__ == '__main__':
    # Example usage (for testing this module directly)
    if ALPHA_VANTAGE_API_KEY:
        print("Alpha Vantage Client - Example Usage")
        symbol_to_test = "IBM" # Example stock symbol
        price_data = get_stock_price(symbol_to_test)
        if price_data:
            if "note" in price_data:
                 print(f"Data for {symbol_to_test}: {price_data['note']}")
            elif "05. price" in price_data:
                print(f"Price for {price_data.get('01. symbol', symbol_to_test)}: {price_data['05. price']}")
            else:
                print(f"Received data for {symbol_to_test}, but price field '05. price' is missing: {price_data}")
        else:
            print(f"Could not retrieve price data for {symbol_to_test}.")
    else:
        print("ALPHA_VANTAGE_API_KEY not set. Cannot run example usage.")