import requests
import logging
import urllib.parse

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tvmaze.com"

class TVMazeError(Exception):
    """Base exception for TVMaze API errors."""
    pass

class TVMazeConnectionError(TVMazeError):
    """Raised when a network problem occurs."""
    pass

class TVMazeAPIError(TVMazeError):
    """Raised when the API returns an error code (HTTP 4xx/5xx)."""
    pass

def search_shows(query: str) -> list[dict]:
    """
    Searches for TV shows on TVMaze.
    Returns a list of show dictionaries.
    """
    encoded_query = urllib.parse.quote(query)
    url = f"{BASE_URL}/search/shows?q={encoded_query}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data:
            show = item.get('show')
            if not show:
                continue
            results.append(show)
        return results
    except requests.exceptions.RequestException as e:
        logger.error(f"Error searching TVMaze for '{query}': {e}")
        raise TVMazeConnectionError(f"Connection error: {e}") from e
    except ValueError as e:
        logger.error(f"Error parsing TVMaze response for '{query}': {e}")
        raise TVMazeAPIError(f"Invalid API response: {e}") from e

def get_show_details(tvmaze_id: int, embed: str | list[str] = None) -> dict:
    """
    Gets detailed information for a specific TV show by its TVMaze ID.
    """
    url = f"{BASE_URL}/shows/{tvmaze_id}"
    params = {}
    if embed:
        params['embed'] = embed

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching TVMaze details for ID {tvmaze_id}: {e}")
        raise TVMazeConnectionError(f"Connection error: {e}") from e

def lookup_show_by_imdb(imdb_id: str) -> dict:
    """
    Looks up a show on TVMaze using an IMDB ID.
    """
    url = f"{BASE_URL}/lookup/shows?imdb={imdb_id}"
    try:
        response = requests.get(url, timeout=10, allow_redirects=True)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error looking up show by IMDB ID {imdb_id}: {e}")
        raise TVMazeConnectionError(f"Connection error: {e}") from e

def lookup_show_by_thetvdb(thetvdb_id: int) -> dict:
    """
    Looks up a show on TVMaze using a TheTVDB ID.
    """
    url = f"{BASE_URL}/lookup/shows?thetvdb={thetvdb_id}"
    try:
        response = requests.get(url, timeout=10, allow_redirects=True)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error looking up show by TheTVDB ID {thetvdb_id}: {e}")
        raise TVMazeConnectionError(f"Connection error: {e}") from e

def get_episode_by_id(episode_id: int) -> dict:
    """
    Gets detailed information for a specific episode by its TVMaze ID.
    """
    url = f"{BASE_URL}/episodes/{episode_id}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching TVMaze episode details for ID {episode_id}: {e}")
        raise TVMazeConnectionError(f"Connection error: {e}") from e

