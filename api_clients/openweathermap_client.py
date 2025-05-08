import aiohttp
import asyncio
import logging
from typing import Optional, Dict, Any

from config import OPENWEATHERMAP_API_KEY # We'll add this to config.py next

logger = logging.getLogger(__name__)

BASE_URL_CURRENT = "http://api.openweathermap.org/data/2.5/weather"
BASE_URL_FORECAST = "http://api.openweathermap.org/data/2.5/forecast" # 5 day / 3 hour forecast

# A simple mapping, more can be added.
# See: https://openweathermap.org/weather-conditions
ICON_TO_EMOJI = {
    "01d": "☀️", "01n": "🌙",  # clear sky
    "02d": "⛅️", "02n": "☁️",  # few clouds
    "03d": "☁️", "03n": "☁️",  # scattered clouds
    "04d": "☁️", "04n": "☁️",  # broken clouds, overcast clouds
    "09d": "🌧️", "09n": "🌧️",  # shower rain
    "10d": "🌦️", "10n": "🌧️",  # rain
    "11d": "⛈️", "11n": "⛈️",  # thunderstorm
    "13d": "❄️", "13n": "❄️",  # snow
    "50d": "🌫️", "50n": "🌫️",  # mist
}

async def get_weather_data(location: str, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
    """
    Fetches current weather and a short forecast for a given location using OpenWeatherMap API.

    Args:
        location: The city name or zip code.
        session: The aiohttp ClientSession.

    Returns:
        A dictionary containing processed weather data, or None if an error occurs.
    """
    if not OPENWEATHERMAP_API_KEY:
        logger.error("OpenWeatherMap API key is not configured.")
        return None

    params_current = {
        "q": location,
        "appid": OPENWEATHERMAP_API_KEY,
        "units": "metric"  # For Celsius. Can add imperial for Fahrenheit later.
    }
    params_forecast = {
        "q": location,
        "appid": OPENWEATHERMAP_API_KEY,
        "units": "metric",
        "cnt": 8 # Roughly 24 hours of forecast (8 * 3-hour intervals)
    }

    processed_data = {}

    try:
        # Fetch current weather
        async with session.get(BASE_URL_CURRENT, params=params_current) as resp:
            if resp.status == 200:
                current_data = await resp.json()
                processed_data["current"] = {
                    "location_name": current_data.get("name"),
                    "country": current_data.get("sys", {}).get("country"),
                    "temp": current_data.get("main", {}).get("temp"),
                    "feels_like": current_data.get("main", {}).get("feels_like"),
                    "temp_min": current_data.get("main", {}).get("temp_min"), # For today's high/low
                    "temp_max": current_data.get("main", {}).get("temp_max"), # For today's high/low
                    "humidity": current_data.get("main", {}).get("humidity"),
                    "pressure": current_data.get("main", {}).get("pressure"),
                    "condition": current_data.get("weather", [{}])[0].get("main"),
                    "description": current_data.get("weather", [{}])[0].get("description"),
                    "icon": current_data.get("weather", [{}])[0].get("icon"),
                    "wind_speed": current_data.get("wind", {}).get("speed"), # m/s
                    "wind_deg": current_data.get("wind", {}).get("deg"),
                    "clouds": current_data.get("clouds", {}).get("all"), # %
                    "sunrise": current_data.get("sys", {}).get("sunrise"), # UTC timestamp
                    "sunset": current_data.get("sys", {}).get("sunset"),   # UTC timestamp
                    "timezone": current_data.get("timezone"), # Shift in seconds from UTC
                    "cod": current_data.get("cod")
                }
                processed_data["current"]["emoji"] = ICON_TO_EMOJI.get(processed_data["current"]["icon"], "❓")
            elif resp.status == 404:
                logger.warning(f"Location not found for '{location}': {await resp.text()}")
                return {"error": "Location not found.", "cod": 404}
            else:
                logger.error(f"Error fetching current weather for '{location}'. Status: {resp.status}, Response: {await resp.text()}")
                return {"error": f"API error: {resp.status}", "cod": resp.status}

        # Fetch forecast data (5 day / 3 hour)
        # We'll take the next few entries for a short forecast
        async with session.get(BASE_URL_FORECAST, params=params_forecast) as resp:
            if resp.status == 200:
                forecast_data = await resp.json()
                forecast_list = []
                if forecast_data.get("list"):
                    for item in forecast_data["list"][:3]: # Next 3 forecasts (9 hours)
                        forecast_list.append({
                            "dt": item.get("dt"),
                            "temp": item.get("main", {}).get("temp"),
                            "condition": item.get("weather", [{}])[0].get("main"),
                            "description": item.get("weather", [{}])[0].get("description"),
                            "icon": item.get("weather", [{}])[0].get("icon"),
                            "emoji": ICON_TO_EMOJI.get(item.get("weather", [{}])[0].get("icon"), "❓")
                        })
                processed_data["forecast"] = forecast_list
            else:
                # Not critical if forecast fails, current weather is more important
                logger.warning(f"Error fetching forecast for '{location}'. Status: {resp.status}, Response: {await resp.text()}")
                processed_data["forecast"] = [] # Empty list if forecast fails

        return processed_data

    except aiohttp.ClientError as e:
        logger.error(f"AIOHTTP client error fetching weather for '{location}': {e}")
        return {"error": "Network error while fetching weather data."}
    except asyncio.TimeoutError:
        logger.error(f"Timeout fetching weather for '{location}'.")
        return {"error": "Request timed out while fetching weather data."}
    except Exception as e:
        logger.exception(f"Unexpected error fetching weather for '{location}': {e}")
        return {"error": "An unexpected error occurred."}

if __name__ == '__main__':
    # Example usage (requires OPENWEATHERMAP_API_KEY in .env and config.py)
    # You would run this part by temporarily adding OPENWEATHERMAP_API_KEY to your .env
    # and ensuring config.py loads it.
    # e.g. OPENWEATHERMAP_API_KEY="your_actual_key"
    async def main():
        async with aiohttp.ClientSession() as session:
            # Ensure config.py is set up to load OPENWEATHERMAP_API_KEY
            # For testing, you might need to temporarily hardcode it or ensure .env is correct
            if not OPENWEATHERMAP_API_KEY:
                print("Please set OPENWEATHERMAP_API_KEY in your .env file and update config.py")
                print("Example: OPENWEATHERMAP_API_KEY='yourkey'")
                return

            location = "London,UK"
            weather = await get_weather_data(location, session)
            if weather and "error" not in weather:
                print(f"Weather for {weather.get('current', {}).get('location_name')}:")
                print(f"  Temp: {weather.get('current', {}).get('temp')}°C, Feels like: {weather.get('current', {}).get('feels_like')}°C")
                print(f"  Condition: {weather.get('current', {}).get('condition')} {weather.get('current', {}).get('emoji')}")
                print(f"  Humidity: {weather.get('current', {}).get('humidity')}%")
                print(f"  Wind: {weather.get('current', {}).get('wind_speed')} m/s")
                print("\n  Forecast (next ~9 hours):")
                for f_item in weather.get("forecast", []):
                    from datetime import datetime, timezone
                    dt_object = datetime.fromtimestamp(f_item['dt'], tz=timezone.utc)
                    # Adjust to local time if timezone info is available from current weather
                    if weather.get('current', {}).get('timezone'):
                        dt_object = dt_object.astimezone(timezone(offset=datetime.fromtimestamp(0, tz=timezone.utc).astimezone().tzinfo.utcoffset(None) + timedelta(seconds=weather['current']['timezone'])))

                    print(f"    {dt_object.strftime('%I:%M %p')}: {f_item['temp']}°C, {f_item['condition']} {f_item['emoji']}")

            elif weather and "error" in weather:
                print(f"Error: {weather['error']}")
            else:
                print("Failed to get weather data.")

    # To run this test:
    # 1. Add OPENWEATHERMAP_API_KEY="your_key" to your .env
    # 2. Ensure config.py loads it (as OPENWEATHERMAP_API_KEY)
    # 3. Uncomment the line below and run `python -m api_clients.openweathermap_client`
    # asyncio.run(main())
    pass