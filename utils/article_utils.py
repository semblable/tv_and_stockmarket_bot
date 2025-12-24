import re
from typing import Optional


_COOKIE_WALL_HINTS = [
    # Generic consent/cookie wording
    "cookie",
    "cookies",
    "consent",
    "privacy",
    "privacy policy",
    "your privacy",
    "manage preferences",
    "manage your preferences",
    "cookie settings",
    "cookie preferences",
    "accept all",
    "reject all",
    "i agree",
    "we use cookies",
    "we and our partners",
    "legitimate interest",
    "personalised",
    "personalized",
    "ad choices",
    "do not sell",
    "gdpr",
    "cmp",
    "iab",
]


def extract_readable_text_from_html(html: str) -> str:
    """
    Best-effort article text extraction from HTML.

    - Removes obvious boilerplate tags (script/style/nav/etc.)
    - Keeps paragraph-ish blocks
    - Returns a single cleaned string

    This is intentionally lightweight (no heavy readability dependencies).
    """
    if not isinstance(html, str) or not html.strip():
        return ""

    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        # If bs4 isn't installed, gracefully return empty.
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        try:
            tag.decompose()
        except Exception:
            pass

    # Try to target common article containers
    main = None
    for selector in [
        "article",
        "main",
        "div[itemprop='articleBody']",
        "div[class*='article']",
        "div[class*='content']",
    ]:
        try:
            found = soup.select_one(selector)
            if found and found.get_text(strip=True):
                main = found
                break
        except Exception:
            continue

    root = main if main is not None else soup.body if soup.body is not None else soup

    # Collect paragraph-like blocks
    parts = []
    for el in root.find_all(["p", "h1", "h2", "h3", "li"]):
        try:
            txt = el.get_text(" ", strip=True)
        except Exception:
            continue
        if not txt:
            continue
        # Filter out very short / UI-ish fragments (but keep headings)
        tag = getattr(el, "name", "") or ""
        if tag not in {"h1", "h2", "h3"}:
            if len(txt) < 40 and not re.search(r"[.!?]", txt):
                continue
        parts.append(txt)

    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def clamp_text(s: str, *, max_chars: int) -> str:
    if not isinstance(s, str):
        return ""
    if max_chars <= 0:
        return ""
    s = s.strip()
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"


def looks_like_html(content_type: Optional[str], text: str) -> bool:
    ct = (content_type or "").lower()
    if "text/html" in ct or "application/xhtml" in ct:
        return True
    # Fallback heuristic
    t = (text or "").lstrip()
    return t.startswith("<!doctype html") or t.startswith("<html") or "<body" in t[:2000].lower()


def extract_canonical_url_from_html(html: str) -> str:
    """
    Best-effort canonical URL extraction from HTML.
    Useful for Google News redirect/article wrapper pages.
    """
    if not isinstance(html, str) or not html.strip():
        return ""

    # <link rel="canonical" href="...">
    m = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    if m:
        return (m.group(1) or "").strip()

    # <meta property="og:url" content="...">
    m = re.search(
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    if m:
        return (m.group(1) or "").strip()

    return ""


def is_probably_cookie_wall(text: str) -> bool:
    """
    Detect pages that are mostly consent/cookie boilerplate.
    Used to avoid treating cookie popups as "article text".
    """
    if not isinstance(text, str):
        return False
    t = text.strip()
    if not t:
        return False

    low = t.lower()

    # If it looks like an article (enough punctuation + length), be conservative.
    if len(t) > 6000 and low.count("cookie") <= 2 and low.count("consent") == 0:
        return False

    hits = 0
    for h in _COOKIE_WALL_HINTS:
        if h in low:
            hits += 1

    # Strong signals
    if low.count("cookie") >= 8:
        return True
    if "we use cookies" in low and ("accept all" in low or "manage" in low or "preferences" in low):
        return True

    # General heuristic: multiple distinct consent hints + not very article-like.
    if hits >= 4 and len(t) < 15000:
        return True

    return False


def is_probably_block_page(text: str) -> bool:
    """
    Detect common anti-bot / access-denied pages so we don't include them as \"ARTICLE\" text.
    """
    if not isinstance(text, str):
        return False
    t = text.strip()
    if not t:
        return False
    low = t.lower()

    # Very common patterns across WAF/CDN blocks
    block_hints = [
        "403 forbidden",
        "access denied",
        "request blocked",
        "not authorized",
        "permission denied",
        "temporarily unavailable",
        "unusual traffic",
        "verify you are human",
        "captcha",
        "cf-ray",
        "cloudflare",
        "akamai",
        "imperva",
        "incapsula",
        "bot detection",
        "security check",
    ]
    hits = 0
    for h in block_hints:
        if h in low:
            hits += 1

    if hits >= 2:
        return True
    # Strong single-signal
    if "403 forbidden" in low or "verify you are human" in low or "captcha" in low:
        return True

    return False


