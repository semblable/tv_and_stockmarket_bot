import re
import io
from typing import Optional

import requests

QUICKCHART_BASE_URL = "https://quickchart.io/chart"


def _parse_minutes(value: str) -> Optional[float]:
    """
    Parse a loose duration string like "4m", "1h 5m", "90m".
    Returns minutes (float) or None if parse fails.
    """
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s:
        return None
    # Normalize separators
    s = s.replace(",", " ")
    # Match tokens like "1h", "5m", "30s"
    tokens = re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", s)
    if not tokens:
        # fallback: try plain minutes
        try:
            return float(s)
        except Exception:
            return None
    total_min = 0.0
    for num_s, unit in tokens:
        try:
            v = float(num_s)
        except Exception:
            continue
        if unit == "h":
            total_min += v * 60.0
        elif unit == "m":
            total_min += v
        elif unit == "s":
            total_min += v / 60.0
    return total_min if total_min > 0 else 0.0


def parse_activity_report_text(content: str) -> Optional[dict]:
    """
    Parse a simple activity report text into a structured dict.
    Expected format:
    Activity report (YYYY-MM-DD to YYYY-MM-DD UTC)
    Total words read: 911
    Total listening time: 4m
    By language:
    French: 911 words, 4m
    """
    if not isinstance(content, str) or not content.strip():
        return None
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        return None

    header = lines[0]
    if not header.lower().startswith("activity report"):
        return None

    m = re.search(r"\(([^)]+)\)", header)
    period_label = m.group(1).strip() if m else ""

    total_words = None
    total_minutes = None
    by_lang = []
    in_lang = False

    for ln in lines[1:]:
        if ln.lower().startswith("by language"):
            in_lang = True
            continue
        if not in_lang:
            m_words = re.search(r"total words read:\s*([0-9,]+)", ln, re.IGNORECASE)
            if m_words:
                total_words = int(m_words.group(1).replace(",", ""))
                continue
            m_time = re.search(r"total listening time:\s*(.+)$", ln, re.IGNORECASE)
            if m_time:
                total_minutes = _parse_minutes(m_time.group(1))
                continue
        else:
            # Language line: "French: 911 words, 4m"
            m_lang = re.match(r"([^:]+):\s*([0-9,]+)\s*words(?:,\s*([0-9hms\s.,]+))?$", ln, re.IGNORECASE)
            if m_lang:
                lang = m_lang.group(1).strip()
                words = int(m_lang.group(2).replace(",", ""))
                mins = _parse_minutes(m_lang.group(3) or "")
                by_lang.append({"language": lang, "words": words, "minutes": mins})

    if total_words is None and total_minutes is None and not by_lang:
        return None

    return {
        "period_label": period_label,
        "totals": {
            "words": total_words,
            "minutes": total_minutes,
        },
        "by_language": by_lang,
    }


def normalize_activity_report_payload(payload: dict) -> Optional[dict]:
    """
    Normalize a structured activity report payload into a consistent dict.
    """
    if not isinstance(payload, dict):
        return None

    data = None
    if isinstance(payload.get("activity_report"), dict):
        data = payload.get("activity_report")
    elif payload.get("type") == "activity_report" or payload.get("report_type") == "activity_report":
        data = payload

    if not isinstance(data, dict):
        return None

    period = data.get("period") or {}
    period_label = data.get("period_label")
    if not period_label and isinstance(period, dict):
        start = str(period.get("start") or "").strip()
        end = str(period.get("end") or "").strip()
        tz = str(period.get("tz") or "").strip()
        if start and end:
            period_label = f"{start} to {end}{(' ' + tz) if tz else ''}".strip()

    totals = data.get("totals") or {}
    total_words = totals.get("words")
    total_minutes = totals.get("minutes")
    try:
        total_words = int(total_words) if total_words is not None else None
    except Exception:
        total_words = None
    try:
        total_minutes = float(total_minutes) if total_minutes is not None else None
    except Exception:
        total_minutes = None

    by_language = []
    for row in (data.get("by_language") or []):
        if not isinstance(row, dict):
            continue
        lang = str(row.get("language") or row.get("lang") or "").strip()
        if not lang:
            continue
        try:
            words = int(row.get("words")) if row.get("words") is not None else 0
        except Exception:
            words = 0
        try:
            minutes = float(row.get("minutes")) if row.get("minutes") is not None else None
        except Exception:
            minutes = None
        by_language.append({"language": lang, "words": words, "minutes": minutes})

    if total_words is None and total_minutes is None and not by_language:
        return None

    return {
        "period_label": period_label or "",
        "totals": {"words": total_words, "minutes": total_minutes},
        "by_language": by_language,
    }


def get_activity_report_chart_image(
    title: str,
    labels: list,
    words: list,
    minutes: Optional[list] = None,
    *,
    chart_width: int = 900,
    chart_height: int = 420,
):
    """
    Generate a dual-axis bar chart for words/minutes by language.
    Returns bytes (io.BytesIO) or None.
    """
    if not labels or not words or len(labels) != len(words):
        return None

    datasets = [
        {
            "label": "words",
            "data": words,
            "backgroundColor": "rgba(99, 102, 241, 0.35)",
            "borderColor": "rgba(99, 102, 241, 1)",
            "borderWidth": 2,
            "borderRadius": 6,
            "yAxisID": "y",
        }
    ]

    has_minutes = bool(minutes) and len(minutes) == len(labels) and any((m or 0) > 0 for m in minutes)
    if has_minutes:
        datasets.append(
            {
                "label": "minutes",
                "data": minutes,
                "backgroundColor": "rgba(16, 185, 129, 0.35)",
                "borderColor": "rgba(16, 185, 129, 1)",
                "borderWidth": 2,
                "borderRadius": 6,
                "yAxisID": "y1",
            }
        )

    chart_config = {
        "type": "bar",
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
                    "grid": {"color": "rgba(0, 0, 0, 0.05)"},
                    "title": {"display": True, "text": "words"},
                },
                **(
                    {
                        "y1": {
                            "beginAtZero": True,
                            "position": "right",
                            "grid": {"display": False},
                            "title": {"display": True, "text": "minutes"},
                        }
                    }
                    if has_minutes
                    else {}
                ),
            },
        },
    }

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
