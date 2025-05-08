# api_clients/tmdb_client.py

import requests
import config # To access TMDB_API_KEY

TMDB_API_KEY = config.TMDB_API_KEY
BASE_URL = "https://api.themoviedb.org/3"

def search_tv_show(query):
    """
    Searches for a TV show on TMDB.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return None
    # Placeholder for search logic
    print(f"Searching TMDB for: {query} (not implemented yet)")
    return []

def get_show_details(show_id):
    """
    Gets detailed information for a specific TV show by its TMDB ID.
    """
    if not TMDB_API_KEY:
        print("Error: TMDB_API_KEY not configured.")
        return None
    # Placeholder for get details logic
    print(f"Getting TMDB details for show ID: {show_id} (not implemented yet)")
    return {}

if __name__ == '__main__':
    # Example usage (for testing this module directly)
    if TMDB_API_KEY:
        print("TMDB Client - Example Usage")
        shows = search_tv_show("The Office")
        if shows:
            # Assuming search_tv_show would return a list of dicts with 'id' and 'name'
            # For now, this part won't run as search_tv_show is a placeholder
            if shows and isinstance(shows, list) and len(shows) > 0 and 'id' in shows[0]:
                 details = get_show_details(shows[0]['id'])
                 print(details)
            else:
                print("Search did not return expected results for example usage.")
        else:
            print("Search returned no results or an error occurred.")
    else:
        print("TMDB_API_KEY not set. Cannot run example usage.")