# api_clients/tmdb_client.py

import requests
import os
import sys
import logging
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import config

logger = logging.getLogger(__name__)

TMDB_API_KEY = config.TMDB_API_KEY
BASE_URL = "https://api.themoviedb.org/3"

class TMDBError(Exception):
    """Base exception for TMDB API errors."""
    pass

class TMDBConnectionError(TMDBError):
    """Raised when a network problem occurs."""
    pass

class TMDBAPIError(TMDBError):
    """Raised when the API returns an error code (HTTP 4xx/5xx)."""
    pass

def search_tv_shows(query: str) -> list[dict]:
    """
    Searches for TV shows on TMDB.
    Returns a list of show dictionaries (each with id, name, poster_path)
    or an empty list on error or no results.
    """
    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not configured.")
        raise TMDBError("TMDB API key is missing.")

    params = {
        'api_key': TMDB_API_KEY,
        'query': query
    }
    search_url = f"{BASE_URL}/search/tv"

    try:
        response = requests.get(search_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        results = []
        if data and 'results' in data:
            for item in data['results']:
                show_info = {
                    'id': item.get('id'),
                    'name': item.get('name'),
                    'first_air_date': item.get('first_air_date'),
                    'overview': item.get('overview'),
                    'poster_path': item.get('poster_path')
                }
                if show_info['id'] is not None and show_info['name'] is not None:
                    results.append(show_info)
        return results
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error during TMDB API request (search_tv_shows): {e}")
        raise TMDBAPIError(f"TMDB API Error: {e}") from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error during TMDB API request (search_tv_shows): {e}")
        raise TMDBConnectionError(f"Connection error: {e}") from e
    except ValueError as e:
        logger.error(f"Error parsing JSON response from TMDB (search_tv_shows): {e}")
        raise TMDBError(f"Invalid API response: {e}") from e

def get_show_details(show_id, append_to_response='next_episode_to_air,last_episode_to_air'):
    """
    Gets detailed information for a specific TV show by its TMDB ID.
    Returns a dictionary with show details.
    Raises TMDBError subclasses on failure.
    """
    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not configured.")
        raise TMDBError("TMDB API key is missing.")

    params = {
        'api_key': TMDB_API_KEY
    }
    if append_to_response:
        params['append_to_response'] = append_to_response
        
    details_url = f"{BASE_URL}/tv/{show_id}"

    try:
        response = requests.get(details_url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return None # Item not found, return None instead of raising
        logger.error(f"HTTP error during TMDB API request (get_show_details for ID {show_id}): {e}")
        raise TMDBAPIError(f"TMDB API Error: {e}") from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error during TMDB API request (get_show_details for ID {show_id}): {e}")
        raise TMDBConnectionError(f"Connection error: {e}") from e
    except ValueError as e:
        logger.error(f"Error parsing JSON response from TMDB (get_show_details for ID {show_id}): {e}")
        raise TMDBError(f"Invalid API response: {e}") from e

def search_movie(query):
    """
    Searches for a movie on TMDB.
    """
    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not configured.")
        raise TMDBError("TMDB API key is missing.")

    params = {
        'api_key': TMDB_API_KEY,
        'query': query
    }
    search_url = f"{BASE_URL}/search/movie"

    try:
        response = requests.get(search_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        results = []
        if data and 'results' in data:
            for item in data['results']:
                if not item.get('release_date'):
                    continue
                movie_info = {
                    'id': item.get('id'),
                    'title': item.get('title'),
                    'release_date': item.get('release_date'),
                    'overview': item.get('overview'),
                    'poster_path': item.get('poster_path')
                }
                if movie_info['id'] is not None and movie_info['title'] is not None:
                    results.append(movie_info)
        return results
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error during TMDB API request (search_movie): {e}")
        raise TMDBAPIError(f"TMDB API Error: {e}") from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error during TMDB API request (search_movie): {e}")
        raise TMDBConnectionError(f"Connection error: {e}") from e
    except ValueError as e:
        logger.error(f"Error parsing JSON response from TMDB (search_movie): {e}")
        raise TMDBError(f"Invalid API response: {e}") from e

def get_movie_details(movie_id, append_to_response='credits,keywords'):
    """
    Gets detailed information for a specific movie by its TMDB ID.
    """
    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not configured.")
        raise TMDBError("TMDB API key is missing.")

    params = {
        'api_key': TMDB_API_KEY
    }
    if append_to_response:
        params['append_to_response'] = append_to_response
        
    details_url = f"{BASE_URL}/movie/{movie_id}"

    try:
        response = requests.get(details_url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return None
        logger.error(f"HTTP error during TMDB API request (get_movie_details for ID {movie_id}): {e}")
        raise TMDBAPIError(f"TMDB API Error: {e}") from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error during TMDB API request (get_movie_details for ID {movie_id}): {e}")
        raise TMDBConnectionError(f"Connection error: {e}") from e
    except ValueError as e:
        logger.error(f"Error parsing JSON response from TMDB (get_movie_details for ID {movie_id}): {e}")
        raise TMDBError(f"Invalid API response: {e}") from e

def get_poster_url(poster_path, size="w92"):
    if poster_path:
        return f"https://image.tmdb.org/t/p/{size}{poster_path}"
    return None

def get_trending_tv_shows(time_window='week'):
    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not configured.")
        raise TMDBError("TMDB API key is missing.")

    if time_window not in ['day', 'week']:
        logger.error(f"Invalid time_window: {time_window}")
        return []

    params = {
        'api_key': TMDB_API_KEY
    }
    trending_url = f"{BASE_URL}/trending/tv/{time_window}"

    try:
        response = requests.get(trending_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        results = []
        if data and 'results' in data:
            for item in data['results']:
                show_info = {
                    'id': item.get('id'),
                    'name': item.get('name'),
                    'first_air_date': item.get('first_air_date'),
                    'overview': item.get('overview'),
                    'poster_path': item.get('poster_path'),
                    'vote_average': item.get('vote_average')
                }
                if show_info['id'] is not None and show_info['name'] is not None:
                    results.append(show_info)
        return results
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error during TMDB API request (get_trending_tv_shows): {e}")
        raise TMDBAPIError(f"TMDB API Error: {e}") from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error during TMDB API request (get_trending_tv_shows): {e}")
        raise TMDBConnectionError(f"Connection error: {e}") from e
    except ValueError as e:
        logger.error(f"Error parsing JSON response from TMDB (get_trending_tv_shows): {e}")
        raise TMDBError(f"Invalid API response: {e}") from e

def get_upcoming_movies(region=None, page=1):
    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not configured.")
        raise TMDBError("TMDB API key is missing.")

    params = {
        'api_key': TMDB_API_KEY,
        'page': page
    }
    if region:
        params['region'] = region
        
    upcoming_url = f"{BASE_URL}/movie/upcoming"

    try:
        response = requests.get(upcoming_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        results = []
        if data and 'results' in data:
            for item in data['results']:
                if not item.get('release_date'):
                    continue
                movie_info = {
                    'id': item.get('id'),
                    'title': item.get('title'),
                    'release_date': item.get('release_date'),
                    'overview': item.get('overview'),
                    'poster_path': item.get('poster_path'),
                    'vote_average': item.get('vote_average')
                }
                if movie_info['id'] is not None and movie_info['title'] is not None:
                    results.append(movie_info)
        
        results.sort(key=lambda x: x['release_date'])
        return results
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error during TMDB API request (get_upcoming_movies): {e}")
        raise TMDBAPIError(f"TMDB API Error: {e}") from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error during TMDB API request (get_upcoming_movies): {e}")
        raise TMDBConnectionError(f"Connection error: {e}") from e
    except ValueError as e:
        logger.error(f"Error parsing JSON response from TMDB (get_upcoming_movies): {e}")
        raise TMDBError(f"Invalid API response: {e}") from e

def get_tv_on_the_air(page=1):
    if not TMDB_API_KEY:
        logger.error("TMDB_API_KEY not configured.")
        raise TMDBError("TMDB API key is missing.")

    params = {
        'api_key': TMDB_API_KEY,
        'page': page
    }
    on_the_air_url = f"{BASE_URL}/tv/on_the_air"

    try:
        response = requests.get(on_the_air_url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        results = []
        if data and 'results' in data:
            for item in data['results']:
                if not item.get('first_air_date'):
                    continue
                show_info = {
                    'id': item.get('id'),
                    'name': item.get('name'),
                    'first_air_date': item.get('first_air_date'),
                    'overview': item.get('overview'),
                    'poster_path': item.get('poster_path'),
                    'vote_average': item.get('vote_average')
                }
                if show_info['id'] is not None and show_info['name'] is not None:
                    results.append(show_info)
        
        results.sort(key=lambda x: x['first_air_date'])
        return results
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error during TMDB API request (get_tv_on_the_air): {e}")
        raise TMDBAPIError(f"TMDB API Error: {e}") from e
    except requests.exceptions.RequestException as e:
        logger.error(f"Connection error during TMDB API request (get_tv_on_the_air): {e}")
        raise TMDBConnectionError(f"Connection error: {e}") from e
    except ValueError as e:
        logger.error(f"Error parsing JSON response from TMDB (get_tv_on_the_air): {e}")
        raise TMDBError(f"Invalid API response: {e}") from e
