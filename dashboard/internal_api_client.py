import requests
import json
from flask import current_app

from dashboard.config import Config

def _make_request(endpoint: str, user_id: str):
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

    current_app.logger.info(f"Making internal API request to: {url}")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)
        return response.json(), None
    except requests.exceptions.ConnectionError as e:
        current_app.logger.error(f"Connection error calling internal API: {e}")
        return None, "Connection error to internal API."
    except requests.exceptions.Timeout as e:
        current_app.logger.error(f"Timeout error calling internal API: {e}")
        return None, "Internal API request timed out."
    except requests.exceptions.HTTPError as e:
        current_app.logger.error(f"HTTP error calling internal API: {e}. Response: {e.response.text}")
        return None, f"Internal API returned an error: {e.response.status_code}"
    except json.JSONDecodeError as e:
        current_app.logger.error(f"JSON decode error from internal API: {e}")
        return None, "Invalid JSON response from internal API."
    except Exception as e:
        current_app.logger.error(f"An unexpected error occurred calling internal API: {e}")
        return None, "An unexpected error occurred."

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

def get_stock_alerts(user_id: str):
    """Fetches stock alerts for a user."""
    endpoint = "/api/internal/user/{user_id}/stock_alerts"
    return _make_request(endpoint, user_id)

def get_user_settings(user_id: str):
    """Fetches user settings."""
    endpoint = "/api/internal/user/{user_id}/settings"
    return _make_request(endpoint, user_id)