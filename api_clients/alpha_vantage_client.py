# api_clients/alpha_vantage_client.py

import requests
import config # To access ALPHA_VANTAGE_API_KEY

ALPHA_VANTAGE_API_KEY = config.ALPHA_VANTAGE_API_KEY
BASE_URL = "https://www.alphavantage.co/query"

def get_stock_price(symbol: str):
    """
    Fetches the current stock price for a given symbol using Alpha Vantage.

    Args:
        symbol: The stock symbol (e.g., "IBM").

    Returns:
        A dictionary containing the stock quote if successful.
        A dictionary with an "error" key if an API limit is reached.
        None if an error occurs (e.g., invalid symbol, network issue).
    """
    if not ALPHA_VANTAGE_API_KEY:
        # Consider logging this instead of printing, or raise an exception
        # if the API key is essential for the application's core functionality.
        print("CRITICAL: ALPHA_VANTAGE_API_KEY not configured.")
        return None

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
            print(f"Alpha Vantage API Error for '{symbol}': {data['Error Message']}")
            return None
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


if __name__ == '__main__':
    print("\n--- Alpha Vantage Client Test ---")

    if not ALPHA_VANTAGE_API_KEY or ALPHA_VANTAGE_API_KEY == "YOUR_API_KEY_HERE": # Added check for placeholder
        print("CRITICAL: ALPHA_VANTAGE_API_KEY is not set or is a placeholder.")
        print("Please set it in config.py or your .env file to run tests.")
        print("--- End of Alpha Vantage Client Test (Skipped) ---\n")
    else:
        print(f"Using API Key: ...{ALPHA_VANTAGE_API_KEY[-4:]}") # Show only last 4 chars for security

        # Test Case 1: Valid Symbol
        print("\n--- Test Case 1: Valid Symbol (IBM) ---")
        valid_symbol = "IBM"
        price_data_valid = get_stock_price(valid_symbol)
        if price_data_valid:
            if price_data_valid.get("error") == "api_limit":
                print(f"API Limit Reached for {valid_symbol}: {price_data_valid.get('message')}")
            elif "05. price" in price_data_valid:
                print(f"Successfully fetched data for {price_data_valid.get('01. symbol', valid_symbol)}:")
                print(f"  Price: {price_data_valid['05. price']}")
                print(f"  Open: {price_data_valid.get('02. open', 'N/A')}")
                print(f"  High: {price_data_valid.get('03. high', 'N/A')}")
                print(f"  Low: {price_data_valid.get('04. low', 'N/A')}")
                print(f"  Volume: {price_data_valid.get('06. volume', 'N/A')}")
                print(f"  Latest trading day: {price_data_valid.get('07. latest trading day', 'N/A')}")
            else:
                print(f"Received data for {valid_symbol}, but key '05. price' is missing or data is malformed: {price_data_valid}")
        else:
            print(f"Failed to retrieve price data for {valid_symbol}.")

        # Test Case 2: Invalid Symbol
        print("\n--- Test Case 2: Invalid Symbol (INVALIDSTOCK) ---")
        invalid_symbol = "INVALIDSTOCKXYZ123" # A clearly invalid symbol
        price_data_invalid = get_stock_price(invalid_symbol)
        if price_data_invalid is None:
            print(f"Correctly handled invalid symbol '{invalid_symbol}'. Function returned None as expected.")
        elif price_data_invalid.get("error") == "api_limit":
             print(f"API Limit Reached for {invalid_symbol}: {price_data_invalid.get('message')}")
        else:
            print(f"Unexpected response for invalid symbol '{invalid_symbol}': {price_data_invalid}")
            print("Expected None for an invalid symbol error from the API.")

        # Test Case 3: API Limit (Conceptual - cannot reliably trigger)
        print("\n--- Test Case 3: API Limit Handling (Conceptual) ---")
        print("The function is designed to return a dictionary like:")
        print("  {'error': 'api_limit', 'message': 'API call frequency limit reached.'}")
        print("if Alpha Vantage returns a 'Note' indicating a limit.")
        print("This allows the calling code (e.g., a cog) to inform the user appropriately.")
        print("To test this, you would need to make enough calls to exceed your API key's quota.")

        # Example of how calling code might use the result
        print("\n--- Example: Handling API Limit in Calling Code ---")
        # Simulate an API limit response
        simulated_api_limit_response = {"error": "api_limit", "message": "Thank you for using Alpha Vantage! Our standard API call frequency is..."}
        if simulated_api_limit_response and simulated_api_limit_response.get("error") == "api_limit":
            print(f"Calling code received API limit: {simulated_api_limit_response['message']}")
            print("Action: Inform user, maybe suggest trying again later.")
        else:
            print("Calling code received other data or error.")

        print("\n--- End of Alpha Vantage Client Test ---\n")