# api_clients/tmdb_client.py

import requests
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import config

TMDB_API_KEY = config.TMDB_API_KEY
BASE_URL = "https://api.themoviedb.org/3"

def search_tv_shows(query: str) -> list[dict]:
    """
    Searches for TV shows on TMDB.
    Returns a list of show dictionaries (each with id, name, poster_path)
    or an empty list on error or no results.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return [] # Return empty list as per requirement for error

    params = {
        'api_key': TMDB_API_KEY,
        'query': query
    }
    search_url = f"{BASE_URL}/search/tv"

    try:
        response = requests.get(search_url, params=params, timeout=15)
        response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)
        data = response.json()
        
        results = []
        if data and 'results' in data:
            for item in data['results']:
                show_info = {
                    'id': item.get('id'),
                    'name': item.get('name'),
                    'first_air_date': item.get('first_air_date'),
                    'overview': item.get('overview'),
                    'poster_path': item.get('poster_path') # Added poster_path
                }
                # Ensure essential fields are present, though TMDB usually provides them
                if show_info['id'] is not None and show_info['name'] is not None:
                    results.append(show_info)
        return results
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error during TMDB API request (search_tv_shows): {e} - Response: {e.response.text if e.response else 'N/A'}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (search_tv_shows): {e}")
        return []
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (search_tv_shows): {e}")
        return []

def get_show_details(show_id, append_to_response='next_episode_to_air,last_episode_to_air'):
    """
    Gets detailed information for a specific TV show by its TMDB ID.
    Allows specifying additional data to append to the response.
    Returns a dictionary with show details or None on error.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return None

    params = {
        'api_key': TMDB_API_KEY
    }
    if append_to_response: # Add append_to_response only if it's provided and not empty
        params['append_to_response'] = append_to_response
        
    details_url = f"{BASE_URL}/tv/{show_id}"

    try:
        response = requests.get(details_url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error during TMDB API request (get_show_details for ID {show_id}): {e} - Response: {e.response.text if e.response else 'N/A'}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (get_show_details for ID {show_id}): {e}")
        return None
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (get_show_details for ID {show_id}): {e}")
        return None

def search_movie(query):
    """
    Searches for a movie on TMDB.
    Returns a list of movie dictionaries or an empty list on error/no results.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return []

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
                movie_info = {
                    'id': item.get('id'),
                    'title': item.get('title'), # Movie uses 'title'
                    'release_date': item.get('release_date'), # Movie uses 'release_date'
                    'overview': item.get('overview'),
                    'poster_path': item.get('poster_path')
                }
                if movie_info['id'] is not None and movie_info['title'] is not None:
                    results.append(movie_info)
        return results
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error during TMDB API request (search_movie): {e} - Response: {e.response.text if e.response else 'N/A'}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (search_movie): {e}")
        return []
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (search_movie): {e}")
        return []

def get_movie_details(movie_id, append_to_response='credits,keywords'):
    """
    Gets detailed information for a specific movie by its TMDB ID.
    Allows specifying additional data to append to the response.
    Returns a dictionary with movie details or None on error.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return None

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
        print(f"HTTP error during TMDB API request (get_movie_details for ID {movie_id}): {e} - Response: {e.response.text if e.response else 'N/A'}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (get_movie_details for ID {movie_id}): {e}")
        return None
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (get_movie_details for ID {movie_id}): {e}")
        return None

def get_poster_url(poster_path, size="w92"):
    """
    Constructs the full URL for a TMDB poster image.
    Args:
        poster_path (str): The poster_path from the TMDB API.
        size (str): The desired image size (e.g., "w92", "w154", "w185", "w342", "w500", "w780", "original").
    Returns:
        str: The full image URL, or None if poster_path is None.
    """
    if poster_path:
        return f"https://image.tmdb.org/t/p/{size}{poster_path}"
    return None

def get_trending_tv_shows(time_window='week'):
    """
    Fetches trending TV shows from TMDB for a given time window.
    Args:
        time_window (str): 'day' or 'week'. Defaults to 'week'.
    Returns:
        list: A list of show dictionaries or an empty list on error/no results.
              Each dictionary contains: 'id', 'name', 'first_air_date',
                                     'poster_path', 'overview', 'vote_average'.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return []

    if time_window not in ['day', 'week']:
        print(f"Invalid time_window: {time_window}. Must be 'day' or 'week'.")
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
                # Ensure essential fields are present
                if show_info['id'] is not None and show_info['name'] is not None:
                    results.append(show_info)
        return results
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error during TMDB API request (get_trending_tv_shows, window: {time_window}): {e} - Response: {e.response.text if e.response else 'N/A'}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (get_trending_tv_shows, window: {time_window}): {e}")
        return []
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (get_trending_tv_shows, window: {time_window}): {e}")
        return []

def get_upcoming_movies(region=None, page=1):
    """
    Fetches upcoming movies from TMDB.
    Args:
        region (str, optional): Specify a ISO 3166-1 code to filter release dates. Must be uppercase.
        page (int, optional): Specify which page to query.
    Returns:
        list: A list of movie dictionaries or an empty list on error/no results.
              Each dictionary contains: 'id', 'title', 'release_date',
                                     'poster_path', 'overview', 'vote_average'.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return []

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
                # Filter out movies without a release date, as they can't be sorted properly
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
        
        # Sort by release_date
        results.sort(key=lambda x: x['release_date'])
        return results
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error during TMDB API request (get_upcoming_movies): {e} - Response: {e.response.text if e.response else 'N/A'}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (get_upcoming_movies): {e}")
        return []
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (get_upcoming_movies): {e}")
        return []

def get_tv_on_the_air(page=1):
    """
    Fetches TV shows that are currently on the air.
    Args:
        page (int, optional): Specify which page to query.
    Returns:
        list: A list of TV show dictionaries or an empty list on error/no results.
              Each dictionary contains: 'id', 'name', 'first_air_date',
                                     'poster_path', 'overview', 'vote_average'.
                                     Note: 'first_air_date' is used for sorting,
                                     actual air dates of episodes making it "on the air"
                                     are not directly in this list endpoint.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return []

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
                # Filter out shows without a first_air_date for consistent sorting
                if not item.get('first_air_date'):
                    continue
                show_info = {
                    'id': item.get('id'),
                    'name': item.get('name'), # TV shows use 'name'
                    'first_air_date': item.get('first_air_date'),
                    'overview': item.get('overview'),
                    'poster_path': item.get('poster_path'),
                    'vote_average': item.get('vote_average')
                }
                if show_info['id'] is not None and show_info['name'] is not None:
                    results.append(show_info)
        
        # Sort by first_air_date
        results.sort(key=lambda x: x['first_air_date'])
        return results
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error during TMDB API request (get_tv_on_the_air): {e} - Response: {e.response.text if e.response else 'N/A'}")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (get_tv_on_the_air): {e}")
        return []
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (get_tv_on_the_air): {e}")
        return []

if __name__ == '__main__':
    # Example usage (for testing this module directly)
    if TMDB_API_KEY:
        print("TMDB Client - Example Usage")
        print("---------------------------")
        
        # Test search_tv_show
        search_query = "The Simpsons"
        print(f"\nSearching for TV show: '{search_query}'...")
        shows = search_tv_shows(search_query)
        
        if shows:
            print(f"Found {len(shows)} show(s) for '{search_query}'.")
            # Print details of the first few shows
            for i, show in enumerate(shows[:3]): # Print first 3 results
                print(f"\nResult {i+1}:")
                print(f"  ID: {show.get('id')}")
                print(f"  Name: {show.get('name')}")
                print(f"  First Air Date: {show.get('first_air_date')}")
                print(f"  Overview: {show.get('overview')[:100]}...") # Truncate overview
                print(f"  Poster Path: {show.get('poster_path')}")
                if show.get('poster_path'):
                    print(f"  Poster URL (w92): {get_poster_url(show.get('poster_path'))}")
                    print(f"  Poster URL (w185): {get_poster_url(show.get('poster_path'), size='w185')}")

            # Test get_show_details with the first result
            if shows[0].get('id'):
                first_show_id = shows[0]['id']
                first_show_name = shows[0]['name']
                print(f"\nGetting details for show ID: {first_show_id} ('{first_show_name}')...")
                details = get_show_details(first_show_id)
                if details:
                    print("\nShow Details:")
                    print(f"  Name: {details.get('name')}")
                    print(f"  Number of Seasons: {details.get('number_of_seasons')}")
                    print(f"  Number of Episodes: {details.get('number_of_episodes')}")
                    print(f"  Status: {details.get('status')}")
                    print(f"  Overview: {details.get('overview')[:100]}...")
                    if details.get('next_episode_to_air'):
                        print(f"  Next Episode to Air: {details['next_episode_to_air'].get('name')} on {details['next_episode_to_air'].get('air_date')}")
                    else:
                        print("  Next Episode to Air: Not available")
                    if details.get('last_episode_to_air'):
                        print(f"  Last Episode Aired: {details['last_episode_to_air'].get('name')} on {details['last_episode_to_air'].get('air_date')}")
                    else:
                        print("  Last Episode Aired: Not available")
                else:
                    print(f"Could not retrieve details for show ID: {first_show_id}")
            else:
                print("First search result did not have an ID to fetch details.")
        elif shows == []: # Explicitly check for empty list (successful request, no results)
             print(f"No shows found for '{search_query}'.")
        else: # This case implies an error occurred and search_tv_show returned None (or other error indicator if changed)
            print(f"Search for '{search_query}' returned no results or an error occurred.")

        # Test search_movie
        movie_search_query = "Inception"
        print(f"\nSearching for movie: '{movie_search_query}'...")
        movies = search_movie(movie_search_query)

        if movies:
            print(f"Found {len(movies)} movie(s) for '{movie_search_query}'.")
            for i, movie in enumerate(movies[:3]):
                print(f"\nResult {i+1}:")
                print(f"  ID: {movie.get('id')}")
                print(f"  Title: {movie.get('title')}")
                print(f"  Release Date: {movie.get('release_date')}")
                print(f"  Overview: {movie.get('overview')[:100]}...")
                print(f"  Poster Path: {movie.get('poster_path')}")
                if movie.get('poster_path'):
                    print(f"  Poster URL (w92): {get_poster_url(movie.get('poster_path'))}")

            # Test get_movie_details with the first result
            if movies[0].get('id'):
                first_movie_id = movies[0]['id']
                first_movie_title = movies[0]['title']
                print(f"\nGetting details for movie ID: {first_movie_id} ('{first_movie_title}')...")
                movie_details = get_movie_details(first_movie_id, append_to_response='credits,keywords')
                if movie_details:
                    print("\nMovie Details:")
                    print(f"  Title: {movie_details.get('title')}")
                    print(f"  Release Date: {movie_details.get('release_date')}")
                    print(f"  Runtime: {movie_details.get('runtime')} minutes")
                    print(f"  Status: {movie_details.get('status')}")
                    print(f"  Overview: {movie_details.get('overview')[:100]}...")
                    if movie_details.get('credits') and movie_details['credits'].get('crew'):
                        directors = [person['name'] for person in movie_details['credits']['crew'] if person['job'] == 'Director']
                        print(f"  Director(s): {', '.join(directors) if directors else 'N/A'}")
                    if movie_details.get('genres'):
                        genres = [genre['name'] for genre in movie_details.get('genres', [])]
                        print(f"  Genres: {', '.join(genres) if genres else 'N/A'}")
                else:
                    print(f"Could not retrieve details for movie ID: {first_movie_id}")
        elif movies == []:
            print(f"No movies found for '{movie_search_query}'.")
        else:
            print(f"Search for '{movie_search_query}' returned no results or an error occurred.")

        print("\n--- Another Search Example: Non-existent show ---")
        non_existent_query = "ThisShowDoesNotExistRandomString123"
        print(f"\nSearching for TV show: '{non_existent_query}'...")
        shows_non_existent = search_tv_shows(non_existent_query)
        if not shows_non_existent:
            print(f"Correctly found no results for '{non_existent_query}'.")
        else:
            print(f"Unexpectedly found results for '{non_existent_query}'.")

        print("\n--- Test get_show_details with an invalid ID ---")
        invalid_show_id = -1
        print(f"\nGetting details for invalid show ID: {invalid_show_id}...")
        details_invalid = get_show_details(invalid_show_id)
        if details_invalid is None:
            print(f"Correctly handled invalid show ID {invalid_show_id} and returned None.")
        else:
            print(f"Unexpectedly got details for invalid show ID {invalid_show_id}.")

        print("\n--- Test get_movie_details with an invalid ID ---")
        invalid_movie_id = -2
        print(f"\nGetting details for invalid movie ID: {invalid_movie_id}...")
        details_invalid_movie = get_movie_details(invalid_movie_id)
        if details_invalid_movie is None:
            print(f"Correctly handled invalid movie ID {invalid_movie_id} and returned None.")
        else:
            print(f"Unexpectedly got details for invalid movie ID {invalid_movie_id}.")

    else:
        print("TMDB_API_KEY not set in .env file. Cannot run example usage.")
        print("Please ensure TMDB_API_KEY is configured in your .env file based on .env.example.")