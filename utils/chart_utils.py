# utils/chart_utils.py

import json
import urllib.parse
import requests

QUICKCHART_BASE_URL = "https://quickchart.io/chart"
QUICKCHART_SHORT_URL = "https://quickchart.io/chart/create"

def generate_stock_chart_url(symbol: str, timespan_label: str, data_points: list, chart_width: int = 800, chart_height: int = 500):
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
                        "maxRotation": 45,
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
        # For direct URL, we need to be careful about length.
        # Remove fill and complex options for fallback
        chart_config["data"]["datasets"][0]["fill"] = False
        chart_config["options"]["plugins"]["title"]["display"] = False # Save space
        del chart_config["options"]["scales"]["y"]["ticks"]["callback"] # remove function for GET
        
        # Use compact separators to save space
        encoded_config = urllib.parse.quote(json.dumps(chart_config, separators=(',', ':')))
        url = f"{QUICKCHART_BASE_URL}?c={encoded_config}&w={chart_width}&h={chart_height}&bkg=white"
        
        if len(url) < 2048:
            return url
        else:
            print(f"Chart URL too long ({len(url)} chars).")
            # Last ditch: decimate data
            if len(labels) > 100:
                step = len(labels) // 50
                chart_config["data"]["labels"] = labels[::step]
                chart_config["data"]["datasets"][0]["data"] = closing_prices[::step]
                encoded_config_short = urllib.parse.quote(json.dumps(chart_config, separators=(',', ':')))
                url_short = f"{QUICKCHART_BASE_URL}?c={encoded_config_short}&w={chart_width}&h={chart_height}&bkg=white"
                if len(url_short) < 2048:
                    return url_short
            
            return None

    except Exception as e:
        print(f"Error in generate_stock_chart_url: {e}")
        return None
