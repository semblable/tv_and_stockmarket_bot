import logging
import random
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class PCGamingWikiError(Exception):
    pass


class PCGamingWikiConnectionError(PCGamingWikiError):
    pass


class PCGamingWikiAPIError(PCGamingWikiError):
    pass


_USER_AGENT = "tv_and_stockmarket_bot/1.0 (pcgamingwiki lookup)"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_BASE = "https://www.pcgamingwiki.com/w/api.php"


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
                raise PCGamingWikiConnectionError(str(e)) from e
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
            raise PCGamingWikiAPIError(f"PCGamingWiki HTTP {r.status_code} for {url}")

        try:
            return r.json()
        except Exception as e:
            raise PCGamingWikiAPIError("PCGamingWiki returned non-JSON response") from e

    if last_exc is not None:
        raise PCGamingWikiConnectionError(str(last_exc)) from last_exc
    raise PCGamingWikiConnectionError("PCGamingWiki request failed")


def search_titles(query: str, *, limit: int = 5) -> List[str]:
    if not query or not isinstance(query, str) or len(query.strip()) < 2:
        return []

    params = {
        "action": "query",
        "list": "search",
        "srsearch": query.strip(),
        "srlimit": max(1, min(int(limit), 10)),
        "format": "json",
    }
    data = _request_json(_BASE, params=params, timeout_s=10.0)
    if not isinstance(data, dict):
        return []

    q = data.get("query")
    if not isinstance(q, dict):
        return []
    s = q.get("search")
    if not isinstance(s, list):
        return []

    out: List[str] = []
    for item in s:
        if not isinstance(item, dict):
            continue
        t = item.get("title")
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
    return out


def page_url(title: str) -> str:
    # PCGamingWiki pages are /wiki/Title_with_underscores
    safe = title.strip().replace(" ", "_")
    return f"https://www.pcgamingwiki.com/wiki/{urllib.parse.quote(safe, safe=':_') }"


def lookup(query: str, *, limit: int = 5) -> Optional[Dict[str, Any]]:
    titles = search_titles(query, limit=limit)
    if not titles:
        return None
    t0 = titles[0]
    return {"title": t0, "url": page_url(t0)}



