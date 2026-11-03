from __future__ import annotations

from pathlib import Path

import pytest

from sqlitex.config import env_file_path, settings_from_env
from sqlitex.errors import ConfigError

BASE = {
    "SQLITEX_API_BASE_URL": "https://api.example.com/v1",
    "SQLITEX_API_KEY": "secret",
    "SQLITEX_MODEL": "modelo",
}


def test_defaults_and_home_resolution(tmp_path):
    settings = settings_from_env({}, home="data")
    assert settings.home == (tmp_path / "data").resolve()
    assert settings.model == "gpt-4o-mini"
    assert settings.max_rows == 50
    assert settings.retrieval == "hybrid"
    assert settings.language == "en"
    assert settings.ai_available is False


def test_relative_home_and_db(tmp_path):
    settings = settings_from_env({"SQLITEX_DB": "mi.sqlite"}, home="otra")
    assert settings.home == (tmp_path / "otra").resolve()
    assert settings.db == (tmp_path / "mi.sqlite").resolve()


def test_remote_provider_requires_api_key():
    settings = settings_from_env({}, home="x")
    assert settings.ai_available is False
    settings = settings_from_env(BASE, home="x")
    assert settings.ai_available is True


def test_local_provider_works_without_key():
    settings = settings_from_env(
        {"SQLITEX_API_BASE_URL": "http://localhost:11434/v1", "SQLITEX_MODEL": "llama3"},
        home="x",
    )
    assert settings.ai_available is True
    assert settings.embeddings_available is True


def test_embeddings_configuration():
    settings = settings_from_env(BASE, home="x")
    assert settings.embeddings_available is True
    assert settings.embed_model == "text-embedding-3-small"

    settings = settings_from_env({**BASE, "SQLITEX_EMBEDDINGS": "off"}, home="x")
    assert settings.embeddings_available is False

    settings = settings_from_env(
        {**BASE, "SQLITEX_EMBED_BASE_URL": "https://embed.example.com/v1"},
        home="x",
    )
    assert settings.embed_base_url == "https://embed.example.com/v1"


def test_env_overrides():
    settings = settings_from_env(
        {
            **BASE,
            "SQLITEX_MAX_ROWS": "7",
            "SQLITEX_RETRIEVAL": "lexical",
            "SQLITEX_TOP_K": "3",
            "SQLITEX_DEBUG": "true",
            "SQLITEX_EXTRA_HEADERS": '{"X-Title": "SQLiteX"}',
        },
        home="x",
    )
    assert settings.max_rows == 7
    assert settings.retrieval == "lexical"
    assert settings.top_k == 3
    assert settings.debug is True
    assert settings.extra_headers == {"X-Title": "SQLiteX"}


@pytest.mark.parametrize(
    "env",
    [
        {"SQLITEX_MAX_ROWS": "muchas"},
        {"SQLITEX_MAX_ROWS": "0"},
        {"SQLITEX_RETRIEVAL": "magia"},
        {"SQLITEX_EMBEDDINGS": "maybe"},
        {"SQLITEX_EXTRA_HEADERS": "no-json"},
        {"SQLITEX_EXTRA_HEADERS": "[]"},
        {"SQLITEX_DEBUG": "a veces"},
    ],
)
def test_invalid_values_raise(env):
    with pytest.raises(ConfigError):
        settings_from_env(env, home="x")


def test_debug_override():
    settings = settings_from_env({"SQLITEX_DEBUG": "false"}, home="x", debug=True)
    assert settings.debug is True


def test_env_file_path_returns_none_without_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert env_file_path() is None


def test_env_file_is_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SQLITEX_MAX_ROWS=11\nSQLITEX_MODEL=from-env\n", encoding="utf-8")
    settings = settings_from_env(None, home="x")
    assert settings.max_rows == 11
    assert settings.model == "from-env"
    assert env_file_path() == tmp_path / ".env"
    assert isinstance(settings.home, Path)
