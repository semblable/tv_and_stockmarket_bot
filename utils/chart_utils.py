# utils/chart_utils.py

import json
import urllib.parse
import requests

QUICKCHART_BASE_URL = "https://quickchart.io/chart"

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
            "title": {
                "display": True,
                "text": f"Stock Chart for {symbol.upper()} ({timespan_label})"
            },
            "scales": {
                "xAxes": [{
                    "type": "time", # Treat labels as time series
                    "time": {
                        # Dynamically choose unit based on timespan or data density
                        # For simplicity, letting Chart.js auto-detect or using a common unit.
                        # More advanced: determine 'unit' (day, month, etc.) based on timespan_label
                        "tooltipFormat": "ll HH:mm" if "min" in timespan_label or "D" in timespan_label and len(labels) > 1 else "ll" # e.g. Sep 4, 1986 8:30 PM or Sep 4, 1986
                    },
                    "ticks": {
                        "autoSkip": True,
                        "maxTicksLimit": 20, # Limit number of x-axis ticks for clarity
                        "maxRotation": x_tick_rotation,
                        "minRotation": x_tick_rotation
                    },
                    "scaleLabel": {
                        "display": True,
                        "labelString": "Date / Time"
                    }
                }],
                "yAxes": [{
                    "scaleLabel": {
                        "display": True,
                        "labelString": "Closing Price (USD)" # Assuming USD, could be parameterized
                    },
                    "ticks": {
                        # Add a callback to format ticks as currency, e.g., $150.00
                        "callback": "function(value, index, values) { return '$' + value.toFixed(2); }"
                    }
                }]
            },
            "legend": {
                "display": True,
                "position": "top"
            },
            "plugins": { # Using 'plugins' for Chart.js v3+ syntax for watermark
                "quickchartWatermark": False # Attempt to disable default watermark if API supports
            }
        }
    }

    # QuickChart.io specific parameters
    params = {
        "chart": json.dumps(chart_config),
        "width": chart_width,
        "height": chart_height,
        "backgroundColor": "white", # Or any other preferred background
        "format": "png" # or 'svg', 'jpg'
        # "key": "YOUR_QUICKCHART_API_KEY" # If you have a paid key for no watermark etc.
    }

    try:
        # For GET requests, parameters are typically URL encoded
        # For POST requests with QuickChart, you send 'chart' as JSON body
        # QuickChart documentation suggests POST for complex charts, but GET works for many.
        # Let's use a GET request for simplicity here, encoding the chart config.
        
        # The 'chart' parameter itself should be a JSON string.
        # The full URL will be like: https://quickchart.io/chart?c={}&width=...
        
        # Correct way to pass chart config for GET request:
        encoded_chart_config = urllib.parse.quote(json.dumps(chart_config))
        request_url = f"{QUICKCHART_BASE_URL}?c={encoded_chart_config}&w={chart_width}&h={chart_height}&bkg=white&f=png"

        # QuickChart recommends POST for larger chart objects.
        # Let's switch to POST to be safe.
        post_payload = {
            "chart": chart_config, # Send the dict directly, requests will handle json.dumps
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png"
        }

        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=10)
        response.raise_for_status() # Check for HTTP errors

        # If the request was successful, the response *body* is the image.
        # QuickChart can also return a URL if you use /chart/create endpoint
        # For /chart endpoint, it directly returns the image.
        # To get a URL, we need to construct it if we are sure the parameters are fine,
        # or use an endpoint that returns a short URL.
        # The simplest way for embedding is to use the direct generation URL as the image source.
        
        # The URL that *would* generate this chart if called via GET:
        final_chart_url = f"{QUICKCHART_BASE_URL}?{urllib.parse.urlencode({'c': json.dumps(chart_config), 'w': chart_width, 'h': chart_height, 'bkg': 'white', 'f': 'png'})}"
        
        # Let's verify the POST request actually worked and we can use its URL.
        # The POST request to /chart returns the image directly.
        # To get a *link* to the image that we can embed, we should construct the GET URL.
        # This GET URL will then be used in the Discord embed.

        # Check if the POST was successful (status 200 and content looks like an image)
        if response.status_code == 200 and response.headers.get('Content-Type', '').startswith('image/'):
            print(f"Successfully generated chart for {symbol} ({timespan_label}) via POST. Constructing GET URL for embedding.")
            return final_chart_url # Return the GET URL that reproduces the chart
        else:
            print(f"Error generating chart for {symbol} ({timespan_label}) with QuickChart. Status: {response.status_code}. Response: {response.text[:200]}")
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
    else:
        print("Failed to generate chart URL for TEST (1D).")

    # Test with many data points (e.g., 1 Year daily)
    sample_data_1y = [(f"2023-{ (i//30)+1:02d}-{ (i%30)+1:02d}", 150 + (i/10) + (i%5)) for i in range(250)] # Approx 250 trading days
    chart_url_1y = generate_stock_chart_url("TEST", "1Y", sample_data_1y)
    if chart_url_1y:
        print(f"Generated chart URL for TEST (1Y) (many points): {chart_url_1y}")
    else:
        print("Failed to generate chart URL for TEST (1Y) (many points).")

    # Test with no data
    chart_url_empty = generate_stock_chart_url("NODATA", "1M", [])
    if not chart_url_empty:
        print("Correctly handled empty data for NODATA.")
    else:
        print(f"Incorrectly generated chart for empty data: {chart_url_empty}")