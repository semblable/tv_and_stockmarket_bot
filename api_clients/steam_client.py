import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class SteamError(Exception):
    """Base exception for Steam store errors."""


class SteamConnectionError(SteamError):
    """Raised when a network problem occurs."""


class SteamAPIError(SteamError):
    """Raised when Steam returns a non-200 or unexpected payload."""


_USER_AGENT = "tv_and_stockmarket_bot/1.0 (steam lookup)"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def app_url(appid: int) -> str:
    try:
        a = int(appid)
    except (TypeError, ValueError):
        a = appid
    return f"https://store.steampowered.com/app/{a}"


def _request_json(
    url: str,
    *,
    params: Optional[dict] = None,
    timeout_s: float = 10.0,
    max_attempts: int = 3,
) -> Any:
    """Best-effort JSON GET with short retries/backoff."""
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
                raise SteamConnectionError(str(e)) from e
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
            raise SteamAPIError(f"Steam HTTP {r.status_code} for {url}")

        try:
            return r.json()
        except Exception as e:
            raise SteamAPIError("Steam returned non-JSON response") from e

    if last_exc is not None:
        raise SteamConnectionError(str(last_exc)) from last_exc
    raise SteamConnectionError("Steam request failed")


def search_store(query: str, *, limit: int = 10, cc: str = "us", l: str = "english") -> List[Dict[str, Any]]:
    """
    Search Steam store (no API key required).

    Endpoint: https://store.steampowered.com/api/storesearch/

    Returns list of dicts:
      { appid, name, type, tiny_image, steam_url }
    """
    if not query or not isinstance(query, str) or len(query.strip()) < 2:
        return []

    url = "https://store.steampowered.com/api/storesearch/"
    params = {
        "term": query.strip(),
        "cc": cc,
        "l": l,
    }

    data = _request_json(url, params=params, timeout_s=10.0)
    if not isinstance(data, dict):
        return []

    items = data.get("items") or []
    if not isinstance(items, list):
        return []

    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            appid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        name = it.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        typ = it.get("type") if isinstance(it.get("type"), str) else None
        tiny_image = it.get("tiny_image") if isinstance(it.get("tiny_image"), str) else None
        out.append(
            {
                "appid": appid,
                "name": name.strip(),
                "type": typ,
                "tiny_image": tiny_image,
                "steam_url": app_url(appid),
            }
        )
        if len(out) >= max(1, min(int(limit), 25)):
            break

    return out


def get_app_details(appid: int, *, cc: str = "us", l: str = "english") -> Optional[Dict[str, Any]]:
    """
    Fetch Steam app details (no API key required).

    Endpoint: https://store.steampowered.com/api/appdetails?appids=XXX

    Returns normalized dict or None if not found.
    """
    try:
        appid_i = int(appid)
    except (TypeError, ValueError):
        raise ValueError("Invalid appid")

    url = "https://store.steampowered.com/api/appdetails"
    params = {"appids": str(appid_i), "cc": cc, "l": l}

    data = _request_json(url, params=params, timeout_s=12.0)
    if not isinstance(data, dict):
        return None

    node = data.get(str(appid_i))
    if not isinstance(node, dict):
        return None
    if not node.get("success"):
        return None

    d = node.get("data")
    if not isinstance(d, dict):
        return None

    name = d.get("name") if isinstance(d.get("name"), str) else None
    short_desc = d.get("short_description") if isinstance(d.get("short_description"), str) else None
    header_image = d.get("header_image") if isinstance(d.get("header_image"), str) else None

    # Release date is a dict: {coming_soon: bool, date: str}
    release_date = None
    coming_soon = None
    rd = d.get("release_date")
    if isinstance(rd, dict):
        if isinstance(rd.get("date"), str) and rd.get("date").strip():
            release_date = rd.get("date").strip()
        if rd.get("coming_soon") is not None:
            coming_soon = bool(rd.get("coming_soon"))

    developers = d.get("developers")
    developer = None
    if isinstance(developers, list):
        devs = [x.strip() for x in developers if isinstance(x, str) and x.strip()]
        developer = ", ".join(devs[:3]) if devs else None

    publishers = d.get("publishers")
    publisher = None
    if isinstance(publishers, list):
        pubs = [x.strip() for x in publishers if isinstance(x, str) and x.strip()]
        publisher = ", ".join(pubs[:3]) if pubs else None

    genres_out: List[str] = []
    genres = d.get("genres")
    if isinstance(genres, list):
        for g in genres:
            if not isinstance(g, dict):
                continue
            desc = g.get("description")
            if isinstance(desc, str) and desc.strip():
                genres_out.append(desc.strip())

    metacritic_score = None
    mc = d.get("metacritic")
    if isinstance(mc, dict):
        try:
            metacritic_score = int(mc.get("score")) if mc.get("score") is not None else None
        except (TypeError, ValueError):
            metacritic_score = None

    platforms = d.get("platforms")
    platforms_out: List[str] = []
    if isinstance(platforms, dict):
        for k in ("windows", "mac", "linux"):
            if platforms.get(k) is True:
                platforms_out.append(k)

    return {
        "appid": appid_i,
        "name": name or str(appid_i),
        "short_description": short_desc,
        "steam_url": app_url(appid_i),
        "header_image": header_image,
        "release_date": release_date,
        "coming_soon": coming_soon,
        "developer": developer,
        "publisher": publisher,
        "genres": genres_out,
        "metacritic_score": metacritic_score,
        "platforms": platforms_out,
    }

