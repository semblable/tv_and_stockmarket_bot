import pytest
import requests

from utils import api_utils


# --- retry ---

def test_retry_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr(api_utils.time, "sleep", lambda s: None)
    calls = []

    @api_utils.retry(attempts=3, backoff=0)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise requests.exceptions.ConnectionError("boom")
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 3  # failed twice, succeeded on the third


def test_retry_exhausts_and_reraises(monkeypatch):
    monkeypatch.setattr(api_utils.time, "sleep", lambda s: None)
    calls = []

    @api_utils.retry(attempts=2, backoff=0)
    def always_fails():
        calls.append(1)
        raise requests.exceptions.Timeout("t")

    with pytest.raises(requests.exceptions.Timeout):
        always_fails()
    assert len(calls) == 2


def test_retry_ignores_unlisted_exceptions():
    calls = []

    @api_utils.retry(attempts=3, backoff=0)
    def raises_value_error():
        calls.append(1)
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        raises_value_error()
    assert len(calls) == 1  # not retried


def test_resilient_get_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(api_utils.time, "sleep", lambda s: None)
    attempts = []

    def fake_get(url, **kwargs):
        attempts.append(url)
        if len(attempts) < 2:
            raise requests.exceptions.ConnectionError("net")
        return "RESP"

    monkeypatch.setattr(api_utils.requests, "get", fake_get)
    assert api_utils.resilient_get("http://example.com") == "RESP"
    assert len(attempts) == 2


# --- ttl_cache ---

def test_ttl_cache_hits_within_ttl():
    calls = []

    @api_utils.ttl_cache(seconds=100)
    def fetch(x):
        calls.append(x)
        return {"value": x}

    assert fetch("AAA") == {"value": "AAA"}
    assert fetch("AAA") == {"value": "AAA"}
    assert calls == ["AAA"]  # second call served from cache


def test_ttl_cache_expires(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(api_utils.time, "monotonic", lambda: clock["t"])
    calls = []

    @api_utils.ttl_cache(seconds=10)
    def fetch(x):
        calls.append(x)
        return {"value": x}

    fetch("A")
    fetch("A")
    assert len(calls) == 1  # cached

    clock["t"] += 11  # advance past TTL
    fetch("A")
    assert len(calls) == 2  # recomputed after expiry


def test_ttl_cache_does_not_cache_none_or_errors():
    calls = []

    @api_utils.ttl_cache(seconds=100)
    def returns_none(x):
        calls.append(x)
        return None

    returns_none("X")
    returns_none("X")
    assert len(calls) == 2  # None never cached

    err_calls = []

    @api_utils.ttl_cache(seconds=100)
    def returns_error(x):
        err_calls.append(x)
        return {"error": "api_limit"}

    returns_error("Y")
    returns_error("Y")
    assert len(err_calls) == 2  # error dicts never cached


def test_ttl_cache_keys_on_arguments():
    calls = []

    @api_utils.ttl_cache(seconds=100)
    def fetch(symbol, interval="1d"):
        calls.append((symbol, interval))
        return {"v": symbol + interval}

    fetch("A", interval="1d")
    fetch("A", interval="5d")  # different kwargs -> separate entry
    fetch("A", interval="1d")  # cached
    assert calls == [("A", "1d"), ("A", "5d")]


def test_clear_all_caches():
    calls = []

    @api_utils.ttl_cache(seconds=100)
    def fetch(x):
        calls.append(x)
        return {"v": x}

    fetch("A")
    fetch("A")
    assert len(calls) == 1
    api_utils.clear_all_caches()
    fetch("A")
    assert len(calls) == 2  # cache cleared, recomputed
