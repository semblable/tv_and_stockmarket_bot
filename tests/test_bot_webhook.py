import time
from bot import _rate_limit


def test_rate_limit_sliding_window():
    key = "test_user_key"
    limit = 3
    window_s = 2

    # First 3 requests should succeed
    assert _rate_limit(key, limit, window_s) is True
    assert _rate_limit(key, limit, window_s) is True
    assert _rate_limit(key, limit, window_s) is True

    # 4th request exceeds limit
    assert _rate_limit(key, limit, window_s) is False

    # After window passes, new requests are allowed
    time.sleep(2.1)
    assert _rate_limit(key, limit, window_s) is True
