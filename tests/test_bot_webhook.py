import base64
import io
import time
from unittest.mock import MagicMock, patch

import pytest
from bot import _rate_limit, bot, flask_app
import config


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


@pytest.fixture
def webhook_client():
    client = flask_app.test_client()
    mock_db = MagicMock()
    mock_db.get_user_id_for_preference_value.return_value = "12345"
    bot.db_manager = mock_db
    bot.loop = MagicMock()
    return client


def _close_coro_sched(coro, loop):
    if hasattr(coro, "close"):
        coro.close()
    return MagicMock()


def test_health_endpoint(webhook_client):
    res_root = webhook_client.get("/")
    assert res_root.status_code == 200
    assert b"Bot is alive and kicking!" in res_root.data

    res_health = webhook_client.get("/health")
    assert res_health.status_code == 200
    assert b"Bot is alive and kicking!" in res_health.data


def test_webhook_report_json_success(webhook_client):
    with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_coro_sched) as mock_sched:
        res = webhook_client.post(
            "/webhook/report/valid_token_1",
            json={"content": "Daily summary text", "title": "Report Title"},
        )
        assert res.status_code == 202
        assert res.json == {"ok": True}
        assert mock_sched.called


def test_webhook_report_multipart_file_with_content(webhook_client):
    with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_coro_sched) as mock_sched:
        data = {
            "content": "Check this file out",
            "title": "Document",
            "file": (io.BytesIO(b"file content here"), "sample.pdf"),
        }
        res = webhook_client.post(
            "/webhook/report/valid_token_2",
            data=data,
            content_type="multipart/form-data",
        )
        assert res.status_code == 202
        assert res.json == {"ok": True}
        assert mock_sched.called


def test_webhook_report_multipart_file_without_content(webhook_client):
    # Uploading only a file with no text content should be accepted
    with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_coro_sched) as mock_sched:
        data = {
            "file": (io.BytesIO(b"pure file data"), "snapshot.png"),
        }
        res = webhook_client.post(
            "/webhook/report/valid_token_3",
            data=data,
            content_type="multipart/form-data",
        )
        assert res.status_code == 202
        assert res.json == {"ok": True}
        assert mock_sched.called


def test_webhook_report_base64_in_json(webhook_client):
    encoded = base64.b64encode(b"hello from base64").decode("utf-8")
    with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_coro_sched) as mock_sched:
        res = webhook_client.post(
            "/webhook/report/valid_token_4",
            json={
                "title": "B64 File",
                "file_name": "test.txt",
                "file_base64": encoded,
            },
        )
        assert res.status_code == 202
        assert res.json == {"ok": True}
        assert mock_sched.called


def test_webhook_report_missing_content_and_file(webhook_client):
    res = webhook_client.post(
        "/webhook/report/valid_token_5",
        json={"content": "   "},
    )
    assert res.status_code == 400
    assert res.json == {"ok": False, "error": "missing_content"}


def test_webhook_report_payload_too_large(webhook_client):
    with patch.object(config, "WEBHOOK_MAX_BYTES", 100):
        large_bytes = b"X" * 200
        res = webhook_client.post(
            "/webhook/report/valid_token_6",
            data={"file": (io.BytesIO(large_bytes), "large.bin")},
            content_type="multipart/form-data",
        )
        assert res.status_code == 413
        assert res.json == {"ok": False, "error": "payload_too_large"}


def test_webhook_report_invalid_token(webhook_client):
    bot.db_manager.get_user_id_for_preference_value.return_value = None
    res = webhook_client.post(
        "/webhook/report/non_existent_token",
        json={"content": "Hello"},
    )
    assert res.status_code == 404
    assert res.json == {"ok": False, "error": "invalid_token"}
