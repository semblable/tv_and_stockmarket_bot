# tests/test_config.py
import os
import tempfile
import pytest
from unittest.mock import patch
from config import _resolve_tmdb_secret, Settings


def test_resolve_tmdb_secret_from_custom_file():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write("custom_file_tmdb_key\n")
        temp_path = f.name

    try:
        resolved = _resolve_tmdb_secret(tmdb_key="fallback_key", tmdb_key_file=temp_path)
        assert resolved == "custom_file_tmdb_key"
    finally:
        os.remove(temp_path)


def test_resolve_tmdb_secret_from_custom_file_env_format():
    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        f.write('TMDB_API_KEY="custom_quoted_key"\n')
        temp_path = f.name

    try:
        resolved = _resolve_tmdb_secret(tmdb_key="fallback_key", tmdb_key_file=temp_path)
        assert resolved == "custom_quoted_key"
    finally:
        os.remove(temp_path)


def test_resolve_tmdb_secret_fallback_to_env():
    resolved = _resolve_tmdb_secret(tmdb_key="env_provided_key", tmdb_key_file="nonexistent_path")
    assert resolved == "env_provided_key"


def test_resolve_tmdb_secret_empty_when_missing():
    resolved = _resolve_tmdb_secret(tmdb_key="", tmdb_key_file="")
    assert resolved == ""


def test_settings_loads_tmdb_from_file_override(tmp_path):
    secret_file = tmp_path / "tmdb_secret.txt"
    secret_file.write_text("secret_from_file_123")

    with patch.dict(os.environ, {
        "DISCORD_BOT_TOKEN": "token",
        "TMDB_API_KEY": "old_env_key",
        "TMDB_API_KEY_FILE": str(secret_file),
        "ALPHA_VANTAGE_API_KEY": "av",
        "OPENWEATHERMAP_API_KEY": "owm"
    }):
        settings = Settings()
        assert settings.TMDB_API_KEY == "secret_from_file_123"


def test_settings_validation_fails_without_tmdb():
    with patch.dict(os.environ, {
        "DISCORD_BOT_TOKEN": "token",
        "TMDB_API_KEY": "",
        "TMDB_API_KEY_FILE": "",
        "ALPHA_VANTAGE_API_KEY": "av",
        "OPENWEATHERMAP_API_KEY": "owm"
    }, clear=True):
        with pytest.raises(Exception):
            Settings()
