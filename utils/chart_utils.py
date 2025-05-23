# utils/chart_utils.py

import json
import urllib.parse
import requests

QUICKCHART_BASE_URL = "https://quickchart.io/chart"
QUICKCHART_SHORT_URL = "https://quickchart.io/chart/create"

def generate_stock_chart_url(symbol: str, timespan_label: str, data_points: list, chart_width: int = 600, chart_height: int = 400):
    """
    Generates a stock chart image URL using QuickChart.io.

    Args:
        symbol: The stock symbol (e.g., "AAPL").
        timespan_label: The timespan label for the chart title (e.g., "1M", "1Y").
        data_points: A list of tuples (timestamp_str, value_float), sorted chronologically.
                     Example: [('2023-01-01', 150.00), ('2023-01-02', 152.50)]
        chart_width: Width of the chart image in pixels.
        chart_height: Height of the chart image in pixels.

    Returns:
        A string URL to the generated chart image if successful.
        None if data_points is empty or an error occurs.
    """
    if not data_points:
        print(f"Error generating chart for {symbol}: No data points provided.")
        return None

    labels = [dp[0] for dp in data_points]
    closing_prices = [dp[1] for dp in data_points]

    # Determine x-axis tick rotation based on number of labels for readability
    x_tick_rotation = 0
    if len(labels) > 30: # For longer timespans like 6M, 1Y with daily data
        x_tick_rotation = 70
    elif len(labels) > 10: # For 1M daily or 5D intraday
        x_tick_rotation = 45

    chart_config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": f"{symbol} Closing Price",
                "data": closing_prices,
                "fill": False,
                "borderColor": "rgb(75, 192, 192)",
                "tension": 0.1,
                "pointRadius": 1, # Smaller points for cleaner look with many data points
                "pointHoverRadius": 5
            }]
        },
        "options": {
            "responsive": True,
            "plugins": {
                "title": {
                    "display": True,
                    "text": f"Stock Chart for {symbol.upper()} ({timespan_label})"
                },
                "legend": {
                    "display": True,
                    "position": "top"
                }
            },
            "scales": {
                "x": {
                    "type": "time",
                    "time": {
                        "tooltipFormat": "MMM DD, YYYY HH:mm" if "min" in timespan_label or "D" in timespan_label and len(labels) > 1 else "MMM DD, YYYY"
                    },
                    "ticks": {
                        "autoSkip": True,
                        "maxTicksLimit": 20,
                        "maxRotation": x_tick_rotation,
                        "minRotation": x_tick_rotation
                    },
                    "title": {
                        "display": True,
                        "text": "Date / Time"
                    }
                },
                "y": {
                    "title": {
                        "display": True,
                        "text": "Closing Price (USD)"
                    },
                    "ticks": {
                        "callback": "function(value, index, values) { return '$' + value.toFixed(2); }"
                    }
                }
            }
        }
    }

    try:
        # Use QuickChart's short URL service to avoid Discord's 2048 character limit
        # This creates a permanent short URL that redirects to the chart
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png"
        }

        # First, try to create a short URL
        response = requests.post(QUICKCHART_SHORT_URL, json=post_payload, timeout=15)
        response.raise_for_status()
        
        if response.status_code == 200:
            response_data = response.json()
            short_url = response_data.get('url')
            if short_url:
                print(f"Successfully generated short chart URL for {symbol} ({timespan_label}): {short_url}")
                return short_url
        
        # Fallback: Try the direct method with a simplified chart for smaller URLs
        print(f"Short URL generation failed for {symbol} ({timespan_label}), trying direct method...")
        
        # Simplify chart config for shorter URL
        simplified_config = {
            "type": "line",
            "data": {
                "labels": labels,
                "datasets": [{
                    "label": f"{symbol}",
                    "data": closing_prices,
                    "borderColor": "rgb(75,192,192)",
                    "fill": False
                }]
            },
            "options": {
                "plugins": {
                    "title": {
                        "display": True,
                        "text": f"{symbol} ({timespan_label})"
                    }
                }
            }
        }
        
        # Try direct POST to get image
        direct_payload = {
            "chart": simplified_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png"
        }
        
        direct_response = requests.post(QUICKCHART_BASE_URL, json=direct_payload, timeout=10)
        direct_response.raise_for_status()
        
        if direct_response.status_code == 200 and direct_response.headers.get('Content-Type', '').startswith('image/'):
            # Construct a simplified GET URL as fallback
            simplified_get_url = f"{QUICKCHART_BASE_URL}?c={urllib.parse.quote(json.dumps(simplified_config))}&w={chart_width}&h={chart_height}"
            
            # Check if the simplified URL is short enough for Discord
            if len(simplified_get_url) <= 2000:  # Leave some buffer under 2048
                print(f"Successfully generated simplified chart URL for {symbol} ({timespan_label}). Length: {len(simplified_get_url)}")
                return simplified_get_url
            else:
                print(f"Chart URL still too long for {symbol} ({timespan_label}): {len(simplified_get_url)} characters")
                return None
        else:
            print(f"Direct chart generation failed for {symbol} ({timespan_label})")
            return None

    except requests.exceptions.Timeout:
        print(f"Timeout error generating chart for {symbol} ({timespan_label}) with QuickChart.")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Request error generating chart for {symbol} ({timespan_label}) with QuickChart: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"JSON encoding error for chart config for {symbol} ({timespan_label}): {e}")
        return None
    except Exception as e: # Catch any other unexpected errors
        print(f"An unexpected error occurred in generate_stock_chart_url for {symbol} ({timespan_label}): {e}")
        return None

if __name__ == '__main__':
    print("--- Testing QuickChart Utility ---")
    # Example data (replace with actual fetched data in cog)
    sample_data_1m = [
        ("2024-04-01", 170.03), ("2024-04-02", 169.87), ("2024-04-03", 170.50),
        ("2024-04-04", 171.11), ("2024-04-05", 172.60), ("2024-04-08", 173.00),
        ("2024-04-09", 172.80), ("2024-04-10", 171.75), ("2024-04-11", 173.90),
        ("2024-04-12", 174.50), ("2024-04-15", 173.20), ("2024-04-16", 172.50),
        ("2024-04-17", 171.90), ("2024-04-18", 170.80), ("2024-04-19", 169.50),
        ("2024-04-22", 170.20), ("2024-04-23", 171.30), ("2024-04-24", 172.00),
        ("2024-04-25", 173.50), ("2024-04-26", 174.00), ("2024-04-29", 175.10),
        ("2024-04-30", 174.80)
    ]
    chart_url_1m = generate_stock_chart_url("TEST", "1M", sample_data_1m)
    if chart_url_1m:
        print(f"Generated chart URL for TEST (1M): {chart_url_1m}")
        print(f"URL Length: {len(chart_url_1m)} characters")
    else:
        print("Failed to generate chart URL for TEST (1M).")

    sample_data_1d_intraday = [
        ("2024-05-07 09:30:00", 180.00), ("2024-05-07 10:00:00", 180.50),
        ("2024-05-07 10:30:00", 180.25), ("2024-05-07 11:00:00", 181.00),
        ("2024-05-07 11:30:00", 180.75), ("2024-05-07 12:00:00", 181.20),
        ("2024-05-07 12:30:00", 181.50), ("2024-05-07 13:00:00", 181.30),
        ("2024-05-07 13:30:00", 181.60), ("2024-05-07 14:00:00", 182.00),
        ("2024-05-07 14:30:00", 181.80), ("2024-05-07 15:00:00", 182.10),
        ("2024-05-07 15:30:00", 182.05), ("2024-05-07 16:00:00", 182.20)
    ]
    chart_url_1d = generate_stock_chart_url("TEST", "1D (60min)", sample_data_1d_intraday)
    if chart_url_1d:
        print(f"Generated chart URL for TEST (1D): {chart_url_1d}")
        print(f"URL Length: {len(chart_url_1d)} characters")
    else:
        print("Failed to generate chart URL for TEST (1D).")

    # Test with many data points (e.g., 1 Year daily)
    sample_data_1y = [(f"2023-{ (i//30)+1:02d}-{ (i%30)+1:02d}", 150 + (i/10) + (i%5)) for i in range(100)] # 100 points to test
    chart_url_1y = generate_stock_chart_url("TEST", "1Y", sample_data_1y)
    if chart_url_1y:
        print(f"Generated chart URL for TEST (1Y) (many points): {chart_url_1y}")
        print(f"URL Length: {len(chart_url_1y)} characters")
    else:
        print("Failed to generate chart URL for TEST (1Y) (many points).")

    # Test with no data
    chart_url_empty = generate_stock_chart_url("NODATA", "1M", [])
    if not chart_url_empty:
        print("Correctly handled empty data for NODATA.")
    else:
        print(f"Incorrectly generated chart for empty data: {chart_url_empty}")