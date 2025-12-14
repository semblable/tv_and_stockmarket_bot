import logging
import random
import re
import time
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

_USER_AGENT = "tv_and_stockmarket_bot/1.0 (author subscriptions)"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _request_json(
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout_s: float = 10.0,
    max_attempts: int = 3,
) -> Any:
    """
    Best-effort JSON GET with retries and backoff.

    Open Library can intermittently time out (including SSL handshake/connect timeouts).
    A few short retries are preferable to failing an entire author-check pass.

    timeout_s is treated as the read timeout; connect timeout is set separately.
    """
    # requests supports (connect_timeout, read_timeout)
    connect_timeout_s = max(5.0, min(15.0, float(timeout_s)))
    read_timeout_s = max(10.0, float(timeout_s))
    timeout: Tuple[float, float] = (connect_timeout_s, read_timeout_s)

    h = {"User-Agent": _USER_AGENT}
    if headers:
        h.update(headers)

    last_exc: Optional[BaseException] = None
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            r = requests.get(url, params=params, headers=h, timeout=timeout)
        except requests.RequestException as e:
            last_exc = e
            if attempt >= attempts:
                raise OpenLibraryConnectionError(str(e)) from e
            sleep_s = min(8.0, (0.6 * (2 ** (attempt - 1))) + random.random() * 0.25)
            time.sleep(sleep_s)
            continue

        # Retry transient status codes.
        if r.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
            retry_after = r.headers.get("Retry-After")
            if retry_after and str(retry_after).isdigit():
                sleep_s = min(10.0, float(retry_after))
            else:
                sleep_s = min(8.0, (0.6 * (2 ** (attempt - 1))) + random.random() * 0.25)
            time.sleep(sleep_s)
            continue

        if r.status_code != 200:
            raise OpenLibraryAPIError(f"OpenLibrary HTTP {r.status_code} for {url}")

        try:
            return r.json()
        except Exception as e:
            raise OpenLibraryAPIError("OpenLibrary returned non-JSON response") from e

    if last_exc is not None:
        raise OpenLibraryConnectionError(str(last_exc)) from last_exc
    raise OpenLibraryConnectionError("OpenLibrary request failed")


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


def cover_image_url(cover_id: int, *, size: str = "L") -> Optional[str]:
    """
    Returns a Covers API URL for a cover id (cover_i from /search.json).
    Size: S|M|L
    """
    try:
        cid = int(cover_id)
    except (TypeError, ValueError):
        return None
    if cid <= 0:
        return None
    sz = (size or "L").strip().upper()
    if sz not in ("S", "M", "L"):
        sz = "L"
    return f"https://covers.openlibrary.org/b/id/{cid}-{sz}.jpg"


def search_books(query: str, *, limit: int = 10, timeout_s: float = 10.0) -> List[Dict[str, Any]]:
    """
    Uses Open Library's Search API to find works by free-text query.
    Endpoint: https://openlibrary.org/search.json?q=...

    Returns list of dicts:
      {
        'work_id', 'title', 'author', 'first_publish_year',
        'cover_id', 'cover_url', 'edition_id', 'isbn', 'pages_median'
      }
    """
    if not query or not isinstance(query, str) or len(query.strip()) < 2:
        return []

    url = "https://openlibrary.org/search.json"
    lim = max(1, min(int(limit), 25))
    params = {
        "q": query.strip(),
        "limit": lim,
        # Keep payload small/fast.
        "fields": ",".join(
            [
                "key",
                "title",
                "author_name",
                "first_publish_year",
                "cover_i",
                "edition_key",
                "isbn",
                "number_of_pages_median",
            ]
        ),
    }
    data = _request_json(url, params=params, timeout_s=max(12.0, timeout_s))

    docs = data.get("docs") or []
    out: List[Dict[str, Any]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        key = doc.get("key")
        wid = _normalize_work_id(key) if isinstance(key, str) else None
        title = doc.get("title") if isinstance(doc.get("title"), str) else None
        if not wid or not title:
            continue

        # author_name is usually a list[str]
        author_val = doc.get("author_name")
        author: Optional[str] = None
        if isinstance(author_val, list):
            names = [a for a in author_val if isinstance(a, str) and a.strip()]
            author = ", ".join(names[:3]) if names else None
        elif isinstance(author_val, str) and author_val.strip():
            author = author_val.strip()

        year = doc.get("first_publish_year")
        try:
            first_publish_year = int(year) if year is not None else None
        except (TypeError, ValueError):
            first_publish_year = None

        cover_id = doc.get("cover_i")
        try:
            cover_id_i = int(cover_id) if cover_id is not None else None
        except (TypeError, ValueError):
            cover_id_i = None

        edition_id: Optional[str] = None
        ed = doc.get("edition_key")
        if isinstance(ed, list):
            for e in ed:
                if isinstance(e, str) and e.strip():
                    edition_id = e.strip()
                    break
        elif isinstance(ed, str) and ed.strip():
            edition_id = ed.strip()

        isbn: Optional[str] = None
        isb = doc.get("isbn")
        if isinstance(isb, list):
            for i in isb:
                if isinstance(i, str) and i.strip():
                    isbn = i.strip()
                    break
        elif isinstance(isb, str) and isb.strip():
            isbn = isb.strip()

        pages_median = doc.get("number_of_pages_median")
        try:
            pages_median_i = int(pages_median) if pages_median is not None else None
        except (TypeError, ValueError):
            pages_median_i = None

        out.append(
            {
                "work_id": wid,
                "title": title,
                "author": author,
                "first_publish_year": first_publish_year,
                "cover_id": cover_id_i,
                "cover_url": cover_image_url(cover_id_i, size="L") if cover_id_i else None,
                "edition_id": edition_id,
                "isbn": isbn,
                "pages_median": pages_median_i,
            }
        )

    return out


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
    data = _request_json(url, params=params, timeout_s=timeout_s)

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
    # Works endpoint can be slower; use a slightly higher default read timeout.
    data = _request_json(url, params=params, timeout_s=max(12.0, timeout_s))

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
    data = _request_json(url, timeout_s=timeout_s)
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


