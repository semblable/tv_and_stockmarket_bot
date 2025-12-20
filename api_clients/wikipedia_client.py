import logging
import random
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class WikipediaError(Exception):
    pass


class WikipediaConnectionError(WikipediaError):
    pass


class WikipediaAPIError(WikipediaError):
    pass


_USER_AGENT = "tv_and_stockmarket_bot/1.0 (wikipedia lookup)"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _request_json(
    url: str,
    *,
    params: Optional[dict] = None,
    timeout_s: float = 10.0,
    max_attempts: int = 3,
) -> Any:
    connect_timeout_s = max(5.0, min(15.0, float(timeout_s)))
    read_timeout_s = max(10.0, float(timeout_s))
    timeout: Tuple[float, float] = (connect_timeout_s, read_timeout_s)

    headers = {"User-Agent": _USER_AGENT}

    last_exc: Optional[BaseException] = None
    attempts = max(1, int(max_attempts))

    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            if attempt >= attempts:
                raise WikipediaConnectionError(str(e)) from e
            time.sleep(min(8.0, (0.6 * (2 ** (attempt - 1))) + random.random() * 0.25))
            continue

        if r.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
            retry_after = r.headers.get("Retry-After")
            if retry_after and str(retry_after).isdigit():
                sleep_s = min(10.0, float(retry_after))
            else:
                sleep_s = min(8.0, (0.6 * (2 ** (attempt - 1))) + random.random() * 0.25)
            time.sleep(sleep_s)
            continue

        if r.status_code != 200:
            raise WikipediaAPIError(f"Wikipedia HTTP {r.status_code} for {url}")

        try:
            return r.json()
        except Exception as e:
            raise WikipediaAPIError("Wikipedia returned non-JSON response") from e

    if last_exc is not None:
        raise WikipediaConnectionError(str(last_exc)) from last_exc
    raise WikipediaConnectionError("Wikipedia request failed")


def search_titles(query: str, *, limit: int = 5) -> List[str]:
    """Wikipedia OpenSearch (namespace 0). Returns list of titles."""
    if not query or not isinstance(query, str) or len(query.strip()) < 2:
        return []

    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": query.strip(),
        "limit": max(1, min(int(limit), 10)),
        "namespace": 0,
        "format": "json",
    }

    data = _request_json(url, params=params, timeout_s=10.0)
    if not isinstance(data, list) or len(data) < 2:
        return []

    titles = data[1]
    if not isinstance(titles, list):
        return []

    out: List[str] = []
    for t in titles:
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
    return out


def get_summary(title: str) -> Optional[Dict[str, Any]]:
    """Wikipedia REST summary for a title."""
    if not title or not isinstance(title, str) or not title.strip():
        return None

    # REST endpoint expects URL-encoded title (spaces allowed but safest to encode)
    enc = urllib.parse.quote(title.strip().replace(" ", "_"), safe="")
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{enc}"

    data = _request_json(url, timeout_s=10.0)
    if not isinstance(data, dict):
        return None

    if data.get("type") == "https://mediawiki.org/wiki/HyperSwitch/errors/not_found":
        return None

    page_title = data.get("title") if isinstance(data.get("title"), str) else title.strip()
    extract = data.get("extract") if isinstance(data.get("extract"), str) else None

    page_url = None
    cu = data.get("content_urls")
    if isinstance(cu, dict):
        desk = cu.get("desktop")
        if isinstance(desk, dict) and isinstance(desk.get("page"), str):
            page_url = desk.get("page")

    thumb_url = None
    thumb = data.get("thumbnail")
    if isinstance(thumb, dict) and isinstance(thumb.get("source"), str):
        thumb_url = thumb.get("source")

    return {
        "title": page_title,
        "extract": extract,
        "url": page_url,
        "thumbnail": thumb_url,
    }


def lookup(query: str, *, limit: int = 5) -> Optional[Dict[str, Any]]:
    """Search then fetch summary for top result."""
    titles = search_titles(query, limit=limit)
    for t in titles:
        try:
            s = get_summary(t)
        except WikipediaError:
            s = None
        if s:
            return s
    return None





