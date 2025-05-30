import requests
import json
from flask import current_app

from config import Config

def _make_api_call(method: str, endpoint: str, user_id: str, payload: dict | None = None) -> tuple[dict | None, str | None]:
    """Helper function to make requests to the internal API."""
    base_url = Config.BOT_INTERNAL_API_URL
    api_key = Config.INTERNAL_API_KEY

    if not base_url or not api_key:
        current_app.logger.error("BOT_INTERNAL_API_URL or INTERNAL_API_KEY is not configured.")
        return None, "API URL or Key not configured"

    headers = {
        "X-Internal-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    # Ensure base_url doesn't have a trailing slash if endpoint starts with one
    if base_url.endswith('/') and endpoint.startswith('/'):
        url = f"{base_url[:-1]}{endpoint.format(user_id=user_id)}"
    elif not base_url.endswith('/') and not endpoint.startswith('/'):
        url = f"{base_url}/{endpoint.format(user_id=user_id)}"
    else:
        url = f"{base_url}{endpoint.format(user_id=user_id)}"

    current_app.logger.info(f"Making internal API {method} request to: {url} with payload: {payload}")

    try:
        response = requests.request(method, url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)
        
        # Handle cases where response might be empty (e.g., 204 No Content for a successful POST/DELETE)
        if response.status_code == 204:
            return {}, None # Return empty dict for successful no-content responses
        if not response.content: # Check if content is empty before trying to parse JSON
             return {}, None # Or handle as an error if JSON is always expected

        return response.json(), None
    except requests.exceptions.ConnectionError as e:
        current_app.logger.error(f"Connection error calling internal API: {e}")
        return None, "Connection error to internal API."
    except requests.exceptions.Timeout as e:
        current_app.logger.error(f"Timeout error calling internal API: {e}")
        return None, "Internal API request timed out."
    except requests.exceptions.HTTPError as e:
        current_app.logger.error(f"HTTP error calling internal API: {e}. Response: {e.response.text if e.response else 'No response body'}")
        error_detail = ""
        if e.response is not None:
            try:
                error_json = e.response.json()
                error_detail = error_json.get("detail", e.response.text)
            except json.JSONDecodeError:
                error_detail = e.response.text
        return None, f"Internal API returned an error: {e.response.status_code if e.response else 'Unknown'}. {error_detail}".strip()
    except json.JSONDecodeError as e:
        current_app.logger.error(f"JSON decode error from internal API: {e}. Response text: {response.text if 'response' in locals() else 'Response object not available'}")
        return None, "Invalid JSON response from internal API."
    except Exception as e:
        current_app.logger.error(f"An unexpected error occurred calling internal API: {e}")
        return None, "An unexpected error occurred."

def _make_request(endpoint: str, user_id: str):
    """Helper function to make GET requests to the internal API."""
    return _make_api_call(method="GET", endpoint=endpoint, user_id=user_id)

def add_tv_show_subscription(user_id: str, tmdb_id: int, title: str, poster_path: str) -> tuple[dict | None, str | None]:
    """Adds a TV show subscription for a user."""
    endpoint = "/api/internal/user/{user_id}/tv_show"
    payload = {
        "tmdb_id": tmdb_id,
        "title": title,
        "poster_path": poster_path
    }
    return _make_api_call(method="POST", endpoint=endpoint, user_id=user_id, payload=payload)

def add_movie_subscription(user_id: str, tmdb_id: int, title: str, poster_path: str) -> tuple[dict | None, str | None]:
    """Adds a movie subscription for a user."""
    endpoint = "/api/internal/user/{user_id}/movie"
    payload = {
        "tmdb_id": tmdb_id,
        "title": title,
        "poster_path": poster_path
    }
    return _make_api_call(method="POST", endpoint=endpoint, user_id=user_id, payload=payload)

def remove_tv_show_subscription(user_id: str, tmdb_id: int) -> tuple[dict | None, str | None]:
    """Removes a TV show subscription for a user."""
    # The endpoint needs user_id to be formatted by _make_api_call, and tmdb_id pre-formatted.
    endpoint = f"/api/internal/user/{{user_id}}/tv_show/{tmdb_id}"
    return _make_api_call(method="DELETE", endpoint=endpoint, user_id=user_id)

def get_tv_subscriptions(user_id: str):
    """Fetches TV subscriptions for a user."""
    endpoint = "/api/internal/user/{user_id}/tv_subscriptions"
    return _make_request(endpoint, user_id)

def get_movie_subscriptions(user_id: str):
    """Fetches movie subscriptions for a user."""
    endpoint = "/api/internal/user/{user_id}/movie_subscriptions"
    return _make_request(endpoint, user_id)

def get_tracked_stocks(user_id: str):
    """Fetches tracked stocks for a user."""
    endpoint = "/api/internal/user/{user_id}/tracked_stocks"
    return _make_request(endpoint, user_id)

def get_tracked_stocks_with_prices(user_id: str):
    """Fetches tracked stocks with current prices and portfolio data for a user."""
    endpoint = "/api/internal/user/{user_id}/tracked_stocks_with_prices"
    return _make_request(endpoint, user_id)

def add_tracked_stock(user_id: str, symbol: str, quantity: float = None, purchase_price: float = None) -> tuple[dict | None, str | None]:
    """Adds a tracked stock for a user."""
    endpoint = "/api/internal/user/{user_id}/tracked_stock"
    payload = {
        "symbol": symbol
    }
    if quantity is not None:
        payload["quantity"] = quantity
    if purchase_price is not None:
        payload["purchase_price"] = purchase_price
    return _make_api_call(method="POST", endpoint=endpoint, user_id=user_id, payload=payload)

def get_stock_alerts(user_id: str):
    """Fetches stock alerts for a user."""
    endpoint = "/api/internal/user/{user_id}/stock_alerts"
    return _make_request(endpoint, user_id)

def get_user_settings(user_id: str):
    """Fetches user settings."""
    endpoint = "/api/internal/user/{user_id}/settings"
    return _make_request(endpoint, user_id)