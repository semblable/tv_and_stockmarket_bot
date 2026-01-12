# utils/chart_utils.py

import json
import urllib.parse
import requests
import io
from typing import Optional

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
        
        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=30)
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
            chart_config["options"]["plugins"]["title"]["display"] = False  # Save space

        if "callback" in chart_config["options"]["scales"]["y"]["ticks"]:
            del chart_config["options"]["scales"]["y"]["ticks"]["callback"]  # remove function for GET

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


def _create_weekly_reading_chart_config(title: str, labels: list, values: list, *, unit: str):
    """
    Creates a simple bar chart config for weekly reading stats.
    """
    if not labels or not values or len(labels) != len(values):
        return None

    # Downsample defensively (should not happen for weekly charts)
    if len(labels) > 31:
        labels = labels[-31:]
        values = values[-31:]

    max_v = 0
    try:
        max_v = max(float(v) for v in values if v is not None)
    except Exception:
        max_v = 0

    suggested_max = None
    if max_v > 0:
        suggested_max = max_v * 1.15

    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": unit,
                    "data": values,
                    "backgroundColor": "rgba(54, 162, 235, 0.35)",
                    "borderColor": "rgba(54, 162, 235, 1)",
                    "borderWidth": 2,
                    "borderRadius": 6,
                }
            ],
        },
        "options": {
            "responsive": True,
            "plugins": {
                "title": {"display": True, "text": title, "font": {"size": 18}},
                "legend": {"display": False},
            },
            "scales": {
                "x": {"grid": {"display": False}},
                "y": {
                    "beginAtZero": True,
                    "grid": {"color": "rgba(0, 0, 0, 0.05)"},
                    **({"suggestedMax": suggested_max} if suggested_max else {}),
                },
            },
        },
    }


def get_weekly_reading_chart_image(
    title: str,
    labels: list,
    values: list,
    *,
    unit: str,
    chart_width: int = 800,
    chart_height: int = 450,
):
    """
    Generates a weekly reading bar chart image and returns it as bytes (io.BytesIO).
    Uses QuickChart.io POST endpoint to retrieve the image directly.
    """
    chart_config = _create_weekly_reading_chart_config(title, labels, values, unit=unit)
    if not chart_config:
        return None

    try:
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png",
        }
        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=30)
        if response.status_code == 200:
            return io.BytesIO(response.content)
        return None
    except Exception:
        return None


def _create_habit_daily_chart_config(title: str, labels: list, values: list):
    """
    Simple daily bar chart for habit check-ins.
    """
    if not labels or not values or len(labels) != len(values):
        return None
    if len(labels) > 62:
        labels = labels[-62:]
        values = values[-62:]

    max_v = 0
    try:
        max_v = max(int(v or 0) for v in values)
    except Exception:
        max_v = 0

    suggested_max = None
    if max_v > 0:
        suggested_max = max_v * 1.25

    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "check-ins",
                    "data": values,
                    "backgroundColor": "rgba(46, 204, 113, 0.35)",
                    "borderColor": "rgba(46, 204, 113, 1)",
                    "borderWidth": 2,
                    "borderRadius": 6,
                }
            ],
        },
        "options": {
            "responsive": True,
            "plugins": {
                "title": {"display": True, "text": title, "font": {"size": 18}},
                "legend": {"display": False},
            },
            "scales": {
                "x": {"grid": {"display": False}},
                "y": {
                    "beginAtZero": True,
                    "grid": {"color": "rgba(0, 0, 0, 0.05)"},
                    **({"suggestedMax": suggested_max} if suggested_max else {}),
                },
            },
        },
    }


def get_habit_daily_chart_image(
    title: str,
    labels: list,
    values: list,
    *,
    chart_width: int = 900,
    chart_height: int = 420,
):
    """
    Generates a habit daily check-ins bar chart as bytes (io.BytesIO).
    """
    chart_config = _create_habit_daily_chart_config(title, labels, values)
    if not chart_config:
        return None
    try:
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png",
        }
        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=30)
        if response.status_code == 200:
            return io.BytesIO(response.content)
        return None
    except Exception:
        return None


def _create_habit_weekday_chart_config(title: str, labels: list, values: list):
    """
    Weekday distribution bar chart for habit check-ins.
    """
    if not labels or not values or len(labels) != len(values):
        return None
    if len(labels) != 7:
        # Expect Mon..Sun
        return None

    max_v = 0
    try:
        max_v = max(int(v or 0) for v in values)
    except Exception:
        max_v = 0

    suggested_max = None
    if max_v > 0:
        suggested_max = max_v * 1.25

    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "check-ins",
                    "data": values,
                    "backgroundColor": "rgba(155, 89, 182, 0.35)",
                    "borderColor": "rgba(155, 89, 182, 1)",
                    "borderWidth": 2,
                    "borderRadius": 6,
                }
            ],
        },
        "options": {
            "responsive": True,
            "plugins": {
                "title": {"display": True, "text": title, "font": {"size": 18}},
                "legend": {"display": False},
            },
            "scales": {
                "x": {"grid": {"display": False}},
                "y": {
                    "beginAtZero": True,
                    "grid": {"color": "rgba(0, 0, 0, 0.05)"},
                    **({"suggestedMax": suggested_max} if suggested_max else {}),
                },
            },
        },
    }


def get_habit_weekday_chart_image(
    title: str,
    labels: list,
    values: list,
    *,
    chart_width: int = 800,
    chart_height: int = 420,
):
    """
    Generates a habit weekday distribution bar chart as bytes (io.BytesIO).
    """
    chart_config = _create_habit_weekday_chart_config(title, labels, values)
    if not chart_config:
        return None
    try:
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png",
        }
        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=30)
        if response.status_code == 200:
            return io.BytesIO(response.content)
        return None
    except Exception:
        return None


def _create_mood_daily_chart_config(title: str, labels: list, mood_values: list, energy_values: Optional[list] = None):
    """
    Line chart for mood (and optional energy) over time.
    Uses `null` values to represent gaps (breaks the line).
    """
    if not labels or mood_values is None or len(labels) != len(mood_values):
        return None
    if energy_values is not None and len(energy_values) != len(labels):
        return None

    # Cap to keep payload small
    if len(labels) > 400:
        labels = labels[-400:]
        mood_values = mood_values[-400:]
        if energy_values is not None:
            energy_values = energy_values[-400:]

    datasets = [
        {
            "label": "mood (avg)",
            "data": mood_values,
            "fill": False,
            "borderColor": "rgba(124, 58, 237, 1)",
            "backgroundColor": "rgba(124, 58, 237, 0.15)",
            "borderWidth": 3,
            "pointRadius": 3,
            "pointHoverRadius": 5,
            "spanGaps": False,
            "tension": 0.25,
        }
    ]
    if energy_values is not None:
        datasets.append(
            {
                "label": "energy (avg)",
                "data": energy_values,
                "fill": False,
                "borderColor": "rgba(16, 185, 129, 1)",
                "backgroundColor": "rgba(16, 185, 129, 0.15)",
                "borderWidth": 2,
                "pointRadius": 2,
                "pointHoverRadius": 4,
                "spanGaps": False,
                "tension": 0.25,
            }
        )

    return {
        "type": "line",
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "responsive": True,
            "plugins": {
                "title": {"display": True, "text": title, "font": {"size": 18}},
                "legend": {"display": True},
            },
            "scales": {
                "x": {"grid": {"display": False}},
                "y": {
                    "beginAtZero": True,
                    "min": 0,
                    "max": 10,
                    "grid": {"color": "rgba(0, 0, 0, 0.06)"},
                    "ticks": {"stepSize": 1},
                },
            },
        },
    }


def get_mood_daily_chart_image(
    title: str,
    labels: list,
    mood_values: list,
    energy_values: Optional[list] = None,
    *,
    chart_width: int = 980,
    chart_height: int = 420,
):
    """
    Generates a mood line chart image (PNG) and returns it as bytes (io.BytesIO).
    """
    chart_config = _create_mood_daily_chart_config(title, labels, mood_values, energy_values)
    if not chart_config:
        return None
    try:
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png",
        }
        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=30)
        if response.status_code == 200:
            return io.BytesIO(response.content)
        return None
    except Exception:
        return None


def _create_todo_daily_created_done_chart_config(title: str, labels: list, created: list, done: list):
    """
    Two-series daily bar chart for created vs done to-dos.
    """
    if not labels or created is None or done is None:
        return None
    if len(labels) != len(created) or len(labels) != len(done):
        return None
    if len(labels) > 62:
        labels = labels[-62:]
        created = created[-62:]
        done = done[-62:]

    max_v = 0
    try:
        max_v = max(max(int(v or 0) for v in created), max(int(v or 0) for v in done))
    except Exception:
        max_v = 0

    suggested_max = None
    if max_v > 0:
        suggested_max = max_v * 1.25

    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "created",
                    "data": created,
                    "backgroundColor": "rgba(52, 152, 219, 0.35)",
                    "borderColor": "rgba(52, 152, 219, 1)",
                    "borderWidth": 2,
                    "borderRadius": 6,
                },
                {
                    "label": "done",
                    "data": done,
                    "backgroundColor": "rgba(46, 204, 113, 0.35)",
                    "borderColor": "rgba(46, 204, 113, 1)",
                    "borderWidth": 2,
                    "borderRadius": 6,
                },
            ],
        },
        "options": {
            "responsive": True,
            "plugins": {
                "title": {"display": True, "text": title, "font": {"size": 18}},
            },
            "scales": {
                "x": {"grid": {"display": False}},
                "y": {
                    "beginAtZero": True,
                    "grid": {"color": "rgba(0, 0, 0, 0.05)"},
                    **({"suggestedMax": suggested_max} if suggested_max else {}),
                },
            },
        },
    }


def get_todo_daily_created_done_chart_image(
    title: str,
    labels: list,
    created: list,
    done: list,
    *,
    chart_width: int = 900,
    chart_height: int = 420,
):
    """
    Generates a created vs done daily bar chart image for to-dos.
    """
    chart_config = _create_todo_daily_created_done_chart_config(title, labels, created, done)
    if not chart_config:
        return None
    try:
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png",
        }
        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=30)
        if response.status_code == 200:
            return io.BytesIO(response.content)
        return None
    except Exception:
        return None


def _create_todo_weekday_done_chart_config(title: str, labels: list, values: list):
    if not labels or not values or len(labels) != len(values):
        return None
    if len(labels) != 7:
        return None

    max_v = 0
    try:
        max_v = max(int(v or 0) for v in values)
    except Exception:
        max_v = 0

    suggested_max = None
    if max_v > 0:
        suggested_max = max_v * 1.25

    return {
        "type": "bar",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "done",
                    "data": values,
                    "backgroundColor": "rgba(241, 196, 15, 0.35)",
                    "borderColor": "rgba(241, 196, 15, 1)",
                    "borderWidth": 2,
                    "borderRadius": 6,
                }
            ],
        },
        "options": {
            "responsive": True,
            "plugins": {
                "title": {"display": True, "text": title, "font": {"size": 18}},
                "legend": {"display": False},
            },
            "scales": {
                "x": {"grid": {"display": False}},
                "y": {
                    "beginAtZero": True,
                    "grid": {"color": "rgba(0, 0, 0, 0.05)"},
                    **({"suggestedMax": suggested_max} if suggested_max else {}),
                },
            },
        },
    }


def get_todo_weekday_done_chart_image(
    title: str,
    labels: list,
    values: list,
    *,
    chart_width: int = 800,
    chart_height: int = 420,
):
    """
    Generates a weekday distribution chart for completed to-dos.
    """
    chart_config = _create_todo_weekday_done_chart_config(title, labels, values)
    if not chart_config:
        return None
    try:
        post_payload = {
            "chart": chart_config,
            "width": chart_width,
            "height": chart_height,
            "backgroundColor": "white",
            "format": "png",
        }
        response = requests.post(QUICKCHART_BASE_URL, json=post_payload, timeout=30)
        if response.status_code == 200:
            return io.BytesIO(response.content)
        return None
    except Exception:
        return None
