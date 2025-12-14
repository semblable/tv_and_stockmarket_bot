import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


class OpenLibraryError(Exception):
    pass


class OpenLibraryConnectionError(OpenLibraryError):
    pass


class OpenLibraryAPIError(OpenLibraryError):
    pass


_AUTHOR_ID_RE = re.compile(r"^OL\d+A$", re.IGNORECASE)
_WORK_ID_RE = re.compile(r"^OL\d+W$", re.IGNORECASE)


def _normalize_author_id(author_key_or_id: str) -> Optional[str]:
    """
    Accepts:
      - 'OL23919A'
      - '/authors/OL23919A'
      - 'https://openlibrary.org/authors/OL23919A'
    Returns: 'OL23919A' (uppercased) or None.
    """
    if not author_key_or_id or not isinstance(author_key_or_id, str):
        return None
    s = author_key_or_id.strip()
    s = s.replace("https://openlibrary.org", "")
    if s.startswith("/authors/"):
        s = s[len("/authors/") :]
    s = s.strip("/")
    s_up = s.upper()
    return s_up if _AUTHOR_ID_RE.match(s_up) else None


def _normalize_work_id(work_key_or_id: str) -> Optional[str]:
    """
    Accepts:
      - 'OL82563W'
      - '/works/OL82563W'
      - 'https://openlibrary.org/works/OL82563W'
    Returns: 'OL82563W' (uppercased) or None.
    """
    if not work_key_or_id or not isinstance(work_key_or_id, str):
        return None
    s = work_key_or_id.strip()
    s = s.replace("https://openlibrary.org", "")
    if s.startswith("/works/"):
        s = s[len("/works/") :]
    s = s.strip("/")
    s_up = s.upper()
    return s_up if _WORK_ID_RE.match(s_up) else None


def author_url(author_id: str) -> str:
    aid = _normalize_author_id(author_id) or author_id
    return f"https://openlibrary.org/authors/{aid}"


def work_url(work_id: str) -> str:
    wid = _normalize_work_id(work_id) or work_id
    return f"https://openlibrary.org/works/{wid}"


def search_authors(query: str, *, limit: int = 10, timeout_s: float = 10.0) -> List[Dict[str, Any]]:
    """
    Uses Open Library's public author search.
    Endpoint: https://openlibrary.org/search/authors.json?q=...
    Returns list of dicts:
      { 'author_id', 'name', 'top_work', 'work_count' }
    """
    if not query or not isinstance(query, str) or len(query.strip()) < 2:
        return []

    url = "https://openlibrary.org/search/authors.json"
    params = {"q": query.strip(), "limit": max(1, min(int(limit), 25))}
    headers = {"User-Agent": "tv_and_stockmarket_bot/1.0 (author subscriptions)"}

    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout_s)
    except requests.RequestException as e:
        raise OpenLibraryConnectionError(str(e)) from e

    if r.status_code != 200:
        raise OpenLibraryAPIError(f"OpenLibrary search_authors HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception as e:
        raise OpenLibraryAPIError("OpenLibrary returned non-JSON response") from e

    docs = data.get("docs") or []
    out: List[Dict[str, Any]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        key = doc.get("key")
        aid = _normalize_author_id(key) if isinstance(key, str) else None
        name = doc.get("name") if isinstance(doc.get("name"), str) else None
        if not aid or not name:
            continue
        out.append(
            {
                "author_id": aid,
                "name": name,
                "top_work": doc.get("top_work") if isinstance(doc.get("top_work"), str) else None,
                "work_count": int(doc.get("work_count")) if isinstance(doc.get("work_count"), (int, float, str)) and str(doc.get("work_count")).isdigit() else None,
            }
        )
    return out


def get_author_works(author_id: str, *, limit: int = 50, timeout_s: float = 10.0) -> List[Dict[str, Any]]:
    """
    Endpoint: https://openlibrary.org/authors/{author_id}/works.json
    Returns list of dicts:
      { 'work_id', 'title', 'first_publish_date' }
    """
    aid = _normalize_author_id(author_id)
    if not aid:
        raise ValueError("Invalid author_id")

    url = f"https://openlibrary.org/authors/{aid}/works.json"
    params = {"limit": max(1, min(int(limit), 100))}
    headers = {"User-Agent": "tv_and_stockmarket_bot/1.0 (author subscriptions)"}

    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout_s)
    except requests.RequestException as e:
        raise OpenLibraryConnectionError(str(e)) from e

    if r.status_code != 200:
        raise OpenLibraryAPIError(f"OpenLibrary get_author_works HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception as e:
        raise OpenLibraryAPIError("OpenLibrary returned non-JSON response") from e

    entries = data.get("entries") or []
    out: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        wid = _normalize_work_id(key) if isinstance(key, str) else None
        title = entry.get("title") if isinstance(entry.get("title"), str) else None
        if not wid or not title:
            continue
        fpd = entry.get("first_publish_date") if isinstance(entry.get("first_publish_date"), str) else None
        out.append({"work_id": wid, "title": title, "first_publish_date": fpd})
    return out


def get_author(author_id: str, *, timeout_s: float = 10.0) -> Dict[str, Any]:
    """
    Endpoint: https://openlibrary.org/authors/{author_id}.json
    Returns the raw author JSON dict (at minimum contains 'name' for most authors).
    """
    aid = _normalize_author_id(author_id)
    if not aid:
        raise ValueError("Invalid author_id")

    url = f"https://openlibrary.org/authors/{aid}.json"
    headers = {"User-Agent": "tv_and_stockmarket_bot/1.0 (author subscriptions)"}

    try:
        r = requests.get(url, headers=headers, timeout=timeout_s)
    except requests.RequestException as e:
        raise OpenLibraryConnectionError(str(e)) from e

    if r.status_code != 200:
        raise OpenLibraryAPIError(f"OpenLibrary get_author HTTP {r.status_code}")

    try:
        data = r.json()
    except Exception as e:
        raise OpenLibraryAPIError("OpenLibrary returned non-JSON response") from e

    return data if isinstance(data, dict) else {}


def get_author_name(author_id: str, *, timeout_s: float = 10.0) -> Optional[str]:
    """
    Convenience wrapper around get_author(). Returns the author 'name' if available.
    """
    data = get_author(author_id, timeout_s=timeout_s)
    nm = data.get("name")
    if isinstance(nm, str) and nm.strip():
        return nm.strip()
    return None


