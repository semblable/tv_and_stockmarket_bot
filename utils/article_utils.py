import re
from typing import Optional


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
