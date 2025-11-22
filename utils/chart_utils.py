# utils/chart_utils.py

import json
import urllib.parse
import requests
import io

QUICKCHART_BASE_URL = "https://quickchart.io/chart"
QUICKCHART_SHORT_URL = "https://quickchart.io/chart/create"

def _create_chart_config(symbol: str, timespan_label: str, data_points: list):
    """
    Helper to create the QuickChart config dictionary.
    """
    if not data_points:
        return None
    
    # Auto-downsample if too many points to prevent payload issues
    # QuickChart can handle thousands, but for a simple static image, 500 is plenty of resolution
    # and keeps payload small.
    MAX_POINTS = 500
    if len(data_points) > MAX_POINTS:
        step = len(data_points) // MAX_POINTS + 1
        # Keep the last point (most recent) always
        downsampled_points = data_points[::step]
        if data_points[-1] not in downsampled_points:
            downsampled_points.append(data_points[-1])
        data_points = downsampled_points
        # print(f"Downsampled chart data for {symbol} from {len(data_points) * step} to {len(data_points)} points.")

    labels = []
    for dp in data_points:
        # Try to simplify labels to "MM-DD" or "HH:MM" to save space/readability
        # Assuming dp[0] is "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS"
        ts = dp[0]
        if len(ts) > 10: # Has time
            labels.append(ts[11:16]) # HH:MM
        elif len(ts) >= 10: # YYYY-MM-DD
            labels.append(ts[5:]) # MM-DD
        else:
            labels.append(ts)

    closing_prices = [dp[1] for dp in data_points]

    # Determine x-axis tick rotation and steps
    x_tick_rotation = 0
    if len(labels) > 30:
        x_tick_rotation = 70
    elif len(labels) > 10:
        x_tick_rotation = 45

    # Determine min/max for Y-axis scaling to look better
    try:
        min_price = min(closing_prices)
        max_price = max(closing_prices)
        y_padding = (max_price - min_price) * 0.05
        y_min = min_price - y_padding
        y_max = max_price + y_padding
    except ValueError:
        y_min = 0
        y_max = 0 # Let auto scale handle it

    chart_config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{
                "label": f"{symbol} Price",
                "data": closing_prices,
                "fill": True,
                "borderColor": "rgba(75, 192, 192, 1)",
                "backgroundColor": "rgba(75, 192, 192, 0.1)",
                "borderWidth": 2,
                "pointRadius": 0, # Hide points for smoother line on high density
                "pointHoverRadius": 5
            }]
        },
        "options": {
            "responsive": True,
            "plugins": {
                "title": {
                    "display": True,
                    "text": f"{symbol.upper()} - {timespan_label}",
                    "font": {"size": 18}
                },
                "legend": {
                    "display": False
                }
            },
            "scales": {
                "x": {
                    "ticks": {
                        "autoSkip": True,
                        "maxTicksLimit": 10,
                        "maxRotation": x_tick_rotation,
                        "minRotation": 45
                    },
                    "grid": {
                        "display": False
                    }
                },
                "y": {
                    "ticks": {
                        "callback": "function(value) { return '$' + value.toFixed(2); }"
                    },
                    "grid": {
                        "color": "rgba(0, 0, 0, 0.05)"
                    }
                }
            }
        }
    }

    # Adjust Y-axis explicitly if we have range
    if y_min > 0 and y_max > y_min:
        chart_config["options"]["scales"]["y"]["min"] = y_min
        chart_config["options"]["scales"]["y"]["max"] = y_max
        
    return chart_config

def get_stock_chart_image(symbol: str, timespan_label: str, data_points: list, chart_width: int = 800, chart_height: int = 500):
    """
    Generates a stock chart image and returns it as bytes (io.BytesIO).
    Uses QuickChart.io POST endpoint to retrieve the image directly.
    """
    chart_config = _create_chart_config(symbol, timespan_label, data_points)
    if not chart_config:
        print(f"Error generating chart config for {symbol}: No data points.")
        return None

    try:
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png"
        }
        
        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=15)
        if response.status_code == 200:
            return io.BytesIO(response.content)
        else:
            # Try to avoid printing binary data if it is one
            error_snippet = response.text[:200] if len(response.text) < 1000 else "Response too long/binary"
            print(f"QuickChart error: {response.status_code}. Partial Response: {error_snippet}")
            return None

    except Exception as e:
        print(f"Error fetching chart image: {e}")
        return None

def generate_stock_chart_url(symbol: str, timespan_label: str, data_points: list, chart_width: int = 800, chart_height: int = 500):
    """
    Generates a stock chart image URL using QuickChart.io.
    Kept for backward compatibility, but prefer get_stock_chart_image for better reliability.
    """
    chart_config = _create_chart_config(symbol, timespan_label, data_points)
    if not chart_config:
        return None

    try:
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png"
        }

        # Attempt Short URL
        try:
            response = requests.post(QUICKCHART_SHORT_URL, json=post_payload, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('url'):
                    return data.get('url')
        except Exception as e:
            print(f"QuickChart short URL failed: {e}")

        # Fallback: Construct Direct URL (simplified)
        # Remove fill and complex options for fallback
        chart_config["data"]["datasets"][0]["fill"] = False
        if "title" in chart_config["options"]["plugins"]:
            chart_config["options"]["plugins"]["title"]["display"] = False # Save space
        
        if "callback" in chart_config["options"]["scales"]["y"]["ticks"]:
            del chart_config["options"]["scales"]["y"]["ticks"]["callback"] # remove function for GET
        
        encoded_config = urllib.parse.quote(json.dumps(chart_config, separators=(',', ':')))
        url = f"{QUICKCHART_BASE_URL}?c={encoded_config}&w={chart_width}&h={chart_height}&bkg=white"
        
        if len(url) < 2048:
            return url
        else:
            print(f"Chart URL too long ({len(url)} chars).")
            return None

    except Exception as e:
        print(f"Error in generate_stock_chart_url: {e}")
        return None
