# api_clients/google_news_rss_client.py

from __future__ import annotations

import html
import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_RSS_SEARCH_URL = "https://news.google.com/rss/search"

# Best-effort: keep this very small to avoid over-querying.
_CACHE_TTL_S = 10 * 60
_MIN_REQUEST_INTERVAL_S = 1.0

_cache_lock = threading.Lock()
_cache: Dict[Tuple[str, int, str, str, str], Tuple[float, List[Dict[str, Any]]]] = {}

_rate_lock = threading.Lock()
_last_request_ts = 0.0


_EUROPEAN_SUFFIX_HINTS = {
    ".L": "London Stock Exchange",
    ".PA": "Euronext Paris",
    ".AS": "Euronext Amsterdam",
    ".MI": "Borsa Italiana",
    ".F": "Frankfurt Stock Exchange",
    ".DE": "Xetra",
    ".WA": "Warsaw Stock Exchange",
}


def _rate_limit_blocking() -> None:
    """Best-effort per-process rate limiting (blocking)."""
    global _last_request_ts
    with _rate_lock:
        now = time.monotonic()
        wait_s = (_last_request_ts + _MIN_REQUEST_INTERVAL_S) - now
        if wait_s > 0:
            time.sleep(wait_s)
        _last_request_ts = time.monotonic()


def _strip_html(s: str) -> str:
    if not isinstance(s, str) or not s:
        return ""
    # RSS <description> often contains HTML. Keep it dependency-free.
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _build_query(symbol: str) -> str:
    """Build a Google News search query that works for US + dotted tickers."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return "stock"

    # Common user inputs: UNH.US, NOV.DE, XTB.WA
    base = sym
    if base.endswith(".US"):
        base = base[:-3]

    parts = sym.split(".")
    ticker = parts[0] if parts and parts[0] else base

    # If an exchange suffix is known, add a light hint (kept short to avoid over-filtering).
    suffix = f".{parts[1]}" if len(parts) >= 2 and parts[1] else ""
    exch_hint = _EUROPEAN_SUFFIX_HINTS.get(suffix, "")

    # Compose query: include both raw and simplified tokens.
    tokens = [sym]
    if base and base != sym:
        tokens.append(base)
    if ticker and ticker not in tokens:
        tokens.append(ticker)

    q = " ".join(tokens)
    if exch_hint:
        q = f"{q} {exch_hint}"

    # Add generic finance keyword to improve relevance.
    q = f"{q} stock"
    return q.strip()


def _make_rss_url(*, q: str, hl: str, gl: str, ceid: str) -> str:
    params = {
        "q": q,
        "hl": hl,
        "gl": gl,
        "ceid": ceid,
    }
    return _GOOGLE_NEWS_RSS_SEARCH_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote_plus)


def get_stock_news(
    symbol: str,
    limit: int = 5,
    *,
    hl: str = "en",
    gl: str = "US",
    ceid: str = "US:en",
    timeout_s: int = 12,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch recent news for a symbol using Google News RSS.

    Returns a list of dicts compatible with the bot's expected schema, or None.

    Dict keys match the existing news providers:
    - title, url, source, time_published, summary, sentiment_label, sentiment_score
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None

    try:
        limit_i = max(1, min(25, int(limit)))
    except Exception:
        limit_i = 5

    cache_key = (sym, limit_i, str(hl or ""), str(gl or ""), str(ceid or ""))
    now = time.time()

    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and (now - hit[0]) <= _CACHE_TTL_S:
            return list(hit[1])

    q = _build_query(sym)
    url = _make_rss_url(q=q, hl=hl, gl=gl, ceid=ceid)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    }

    try:
        _rate_limit_blocking()
        resp = requests.get(url, headers=headers, timeout=int(timeout_s), allow_redirects=True)
        if resp.status_code >= 400:
            logger.warning("Google News RSS HTTP %s for %s", resp.status_code, sym)
            return None

        xml_text = resp.text or ""
        if not xml_text.strip():
            return None

        root = ET.fromstring(xml_text)
    except Exception as e:
        logger.warning("Google News RSS error for %s: %s", sym, e)
        return None

    channel = root.find("channel")
    if channel is None:
        return None

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for item in channel.findall("item"):
        if len(out) >= limit_i:
            break

        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = (item.findtext("description") or "").strip()

        source_el = item.find("source")
        source_name = "Google News"
        if source_el is not None and (source_el.text or "").strip():
            source_name = (source_el.text or "").strip()

        # Best-effort published time normalization
        pub_norm = pub
        try:
            dt = parsedate_to_datetime(pub)
            if dt is not None:
                # keep a simple format consistent with the rest of the bot
                pub_norm = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pub_norm = pub

        summary = _strip_html(desc)

        if not title and not link:
            continue

        # De-dupe by link (preferred), fallback to title.
        dedupe_key = link or title
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        out.append(
            {
                "title": title or "Untitled",
                "url": link,
                "source": source_name,
                "time_published": pub_norm,
                "summary": summary,
                "sentiment_label": "N/A",
                "sentiment_score": "N/A",
            }
        )

    if not out:
        return None

    with _cache_lock:
        _cache[cache_key] = (now, list(out))

    return out





