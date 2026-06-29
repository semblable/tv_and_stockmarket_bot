# utils/api_utils.py
"""
Lightweight, dependency-free resilience helpers for the synchronous API clients.

Two tools are provided:

* ``retry`` / ``resilient_get`` — retry transient network failures with
  exponential backoff. ``resilient_get`` is a drop-in replacement for
  ``requests.get`` that retries on :class:`requests.exceptions.RequestException`
  (timeouts, connection errors) and re-raises the last exception once attempts
  are exhausted, so the caller's existing ``try/except`` keeps working unchanged.

* ``ttl_cache`` — a small thread-safe time-to-live cache decorator. The API
  clients run inside ``loop.run_in_executor`` worker threads, so the cache is
  guarded by a lock. Only "good" results are cached: ``None``, empty
  collections, and ``{"error": ...}`` dicts are never stored, preserving the
  existing ``None`` / error-dict contracts of every wrapped function.

Intentionally stdlib-only (no ``tenacity`` / ``cachetools``) to keep the
project's dependency footprint small.
"""

import functools
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

# Registry of every ttl_cache instance's clear function, so tests (and any
# future "flush caches" command) can reset all caches at once.
_cache_clearers: list = []


def clear_all_caches() -> None:
    """Clear every ttl_cache created in this process. Mainly for tests."""
    for clear in _cache_clearers:
        clear()


def retry(
    attempts: int = 3,
    backoff: float = 0.5,
    backoff_factor: float = 2.0,
    max_backoff: float = 8.0,
    exceptions: tuple = (requests.exceptions.RequestException,),
):
    """
    Decorator that retries ``func`` on the given exception types with
    exponential backoff. Re-raises the last exception after the final attempt
    (it does NOT swallow the error), so callers see identical failure behaviour
    to an un-decorated call once retries are exhausted.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = backoff
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt >= attempts:
                        logger.warning(
                            "%s failed after %d attempt(s): %s",
                            func.__name__, attempts, exc,
                        )
                        raise
                    logger.info(
                        "%s attempt %d/%d failed (%s); retrying in %.1fs",
                        func.__name__, attempt, attempts, exc, delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_backoff)
            # Unreachable: the loop either returns or raises.
            raise RuntimeError("retry loop exited unexpectedly")

        return wrapper

    return decorator


@retry()
def resilient_get(url, **kwargs):
    """``requests.get`` with retry + exponential backoff on transient errors."""
    return requests.get(url, **kwargs)


def _is_cacheable(result) -> bool:
    """
    Decide whether a client result is worth caching.

    We deliberately skip falsy results (None, ``[]``, ``{}``, ``""``) and any
    dict carrying an ``"error"`` key — these represent transient failures, rate
    limits, or "no data" and should be re-fetched next time.
    """
    if not result:
        return False
    if isinstance(result, dict) and "error" in result:
        return False
    return True


def _make_key(args, kwargs):
    """Build a hashable cache key from positional + keyword args."""
    return (args, tuple(sorted(kwargs.items())))


def ttl_cache(seconds: float, maxsize: int = 256):
    """
    Thread-safe time-to-live cache decorator.

    Caches successful results (see :func:`_is_cacheable`) keyed on the call
    arguments for ``seconds``. When the cache reaches ``maxsize`` the entry
    closest to expiry is evicted. The wrapped function gains a ``cache_clear()``
    attribute, primarily for tests.
    """
    def decorator(func):
        cache: dict = {}
        lock = threading.Lock()

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                key = _make_key(args, kwargs)
            except TypeError:
                # Unhashable arguments: skip the cache entirely.
                return func(*args, **kwargs)

            now = time.monotonic()
            with lock:
                entry = cache.get(key)
                if entry is not None:
                    value, expires_at = entry
                    if now < expires_at:
                        return value
                    del cache[key]

            result = func(*args, **kwargs)

            if _is_cacheable(result):
                with lock:
                    if len(cache) >= maxsize and key not in cache:
                        oldest = min(cache, key=lambda k: cache[k][1])
                        del cache[oldest]
                    cache[key] = (result, time.monotonic() + seconds)

            return result

        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        _cache_clearers.append(cache.clear)
        return wrapper

    return decorator
