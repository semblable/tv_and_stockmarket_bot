# api_clients/tmdb_client.py

import requests
import config # To access TMDB_API_KEY

TMDB_API_KEY = config.TMDB_API_KEY
BASE_URL = "https://api.themoviedb.org/3"

def search_tv_show(query):
    """
    Searches for a TV show on TMDB.
    Returns a list of show dictionaries or an empty list on error/no results.
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
        response = requests.get(search_url, params=params)
        response.raise_for_status() # Raises an HTTPError for bad responses (4XX or 5XX)
        data = response.json()
        
        results = []
        if data and 'results' in data:
            for item in data['results']:
                show_info = {
                    'id': item.get('id'),
                    'name': item.get('name'),
                    'first_air_date': item.get('first_air_date'),
                    'overview': item.get('overview')
                }
                # Ensure essential fields are present, though TMDB usually provides them
                if show_info['id'] is not None and show_info['name'] is not None:
                    results.append(show_info)
        return results
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (search_tv_show): {e}")
        return []
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (search_tv_show): {e}")
        return []

def get_show_details(show_id):
    """
    Gets detailed information for a specific TV show by its TMDB ID.
    Includes next_episode_to_air and last_episode_to_air.
    Returns a dictionary with show details or None on error.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return None

    params = {
        'api_key': TMDB_API_KEY,
        'append_to_response': 'next_episode_to_air,last_episode_to_air'
    }
    details_url = f"{BASE_URL}/tv/{show_id}"

    try:
        response = requests.get(details_url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error during TMDB API request (get_show_details for ID {show_id}): {e}")
        return None
    except ValueError as e: # Includes JSONDecodeError
        print(f"Error parsing JSON response from TMDB (get_show_details for ID {show_id}): {e}")
        return None

if __name__ == '__main__':
    # Example usage (for testing this module directly)
    if TMDB_API_KEY:
        print("TMDB Client - Example Usage")
        print("---------------------------")
        
        # Test search_tv_show
        search_query = "The Simpsons"
        print(f"\nSearching for TV show: '{search_query}'...")
        shows = search_tv_show(search_query)
        
        if shows:
            print(f"Found {len(shows)} show(s) for '{search_query}'.")
            # Print details of the first few shows
            for i, show in enumerate(shows[:3]): # Print first 3 results
                print(f"\nResult {i+1}:")
                print(f"  ID: {show.get('id')}")
                print(f"  Name: {show.get('name')}")
                print(f"  First Air Date: {show.get('first_air_date')}")
                print(f"  Overview: {show.get('overview')[:100]}...") # Truncate overview
            
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
            
        print("\n--- Another Search Example: Non-existent show ---")
        non_existent_query = "ThisShowDoesNotExistRandomString123"
        print(f"\nSearching for TV show: '{non_existent_query}'...")
        shows_non_existent = search_tv_show(non_existent_query)
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

    else:
        print("TMDB_API_KEY not set in .env file. Cannot run example usage.")
        print("Please ensure TMDB_API_KEY is configured in your .env file based on .env.example.")