"""Load configuration from environment variables / the .env file."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import find_dotenv, load_dotenv

from sqlitex.errors import ConfigError

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"

_TRUE = {"1", "true", "yes", "y", "on", "si"}
_FALSE = {"0", "false", "no", "n", "off"}


def _is_keyless_endpoint(url: str) -> bool:
    """Treat local endpoints as usable without an API key."""

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http":
        return True
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"} or host.endswith(
        ".local"
    )


def _text(env: Mapping[str, str], name: str, default: str = "") -> str:
    raw = env.get(name)
    if raw is None:
        return default
    raw = str(raw).strip()
    return raw if raw else default


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer (current value: {raw!r}).") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be greater than or equal to {minimum} (current value: {value}).")
    return value


def _float(env: Mapping[str, str], name: str, default: float, *, minimum: float | None = None) -> float:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number (current value: {raw!r}).") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be greater than or equal to {minimum} (current value: {value}).")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _text(env, name).lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} must be true/false (current value: {raw!r}).")


def _choice(env: Mapping[str, str], name: str, default: str, allowed: set[str]) -> str:
    raw = _text(env, name, default).lower()
    if raw not in allowed:
        options = ", ".join(sorted(allowed))
        raise ConfigError(f"{name} must be one of: {options} (current value: {raw!r}).")
    return raw


def _headers(env: Mapping[str, str], name: str) -> dict[str, str]:
    raw = _text(env, name)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} must be a valid JSON object.") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{name} must be a JSON object (key -> value).")
    return {str(k): str(v) for k, v in data.items()}


@dataclass(frozen=True)
class Settings:
    """Effective application configuration."""

    home: Path
    db: Path | None
    api_base_url: str
    api_key: str
    model: str
    temperature: float
    timeout: float
    max_retries: int
    json_mode: bool
    extra_headers: dict[str, str]
    embed_model: str
    embed_base_url: str
    embed_api_key: str
    embeddings: str
    language: str
    max_rows: int
    query_timeout: float
    sql_retries: int
    retrieval: str
    top_k: int
    all_tables_threshold: int
    context_max_chars: int
    history_turns: int
    enrich_max_tables: int
    enrich_workers: int
    sample_rows: int
    profile_sample_rows: int
    profile_max_columns: int
    row_count_timeout: float
    debug: bool

    @property
    def ai_available(self) -> bool:
        return bool(
            self.api_base_url and self.model and (self.api_key or _is_keyless_endpoint(self.api_base_url))
        )

    @property
    def embeddings_available(self) -> bool:
        return bool(
            self.embeddings != "off"
            and self.embed_base_url
            and self.embed_model
            and (self.embed_api_key or _is_keyless_endpoint(self.embed_base_url))
        )


def settings_from_env(
    env: Mapping[str, str] | None = None,
    *,
    home: Path | str | None = None,
    debug: bool | None = None,
) -> Settings:
    """Build the configuration from `.env` and the process environment."""

    load_dotenv(find_dotenv(usecwd=True), override=False)
    source: Mapping[str, str] = os.environ if env is None else env

    home_value = Path(home) if home is not None else Path(_text(source, "SQLITEX_HOME", ".sqlitex"))
    home_path = home_value.expanduser()
    if not home_path.is_absolute():
        home_path = (Path.cwd() / home_path).resolve()

    db_raw = _text(source, "SQLITEX_DB")
    db_path = Path(db_raw).expanduser().resolve() if db_raw else None

    api_base_url = _text(source, "SQLITEX_API_BASE_URL", DEFAULT_BASE_URL)
    api_key = _text(source, "SQLITEX_API_KEY")

    return Settings(
        home=home_path,
        db=db_path,
        api_base_url=api_base_url,
        api_key=api_key,
        model=_text(source, "SQLITEX_MODEL", DEFAULT_MODEL),
        temperature=_float(source, "SQLITEX_TEMPERATURE", 0.0, minimum=0.0),
        timeout=_float(source, "SQLITEX_TIMEOUT", 60.0, minimum=1.0),
        max_retries=_int(source, "SQLITEX_MAX_RETRIES", 2, minimum=0),
        json_mode=_bool(source, "SQLITEX_JSON_MODE", False),
        extra_headers=_headers(source, "SQLITEX_EXTRA_HEADERS"),
        embed_model=_text(source, "SQLITEX_EMBED_MODEL", DEFAULT_EMBED_MODEL),
        embed_base_url=_text(source, "SQLITEX_EMBED_BASE_URL", api_base_url),
        embed_api_key=_text(source, "SQLITEX_EMBED_API_KEY", api_key),
        embeddings=_choice(source, "SQLITEX_EMBEDDINGS", "auto", {"auto", "on", "off"}),
        language=_text(source, "SQLITEX_LANGUAGE", "en").lower(),
        max_rows=_int(source, "SQLITEX_MAX_ROWS", 50, minimum=1),
        query_timeout=_float(source, "SQLITEX_QUERY_TIMEOUT", 10.0, minimum=0.1),
        sql_retries=_int(source, "SQLITEX_SQL_RETRIES", 2, minimum=0),
        retrieval=_choice(source, "SQLITEX_RETRIEVAL", "hybrid", {"hybrid", "lexical", "vector", "off"}),
        top_k=_int(source, "SQLITEX_TOP_K", 6, minimum=1),
        all_tables_threshold=_int(source, "SQLITEX_ALL_TABLES_THRESHOLD", 6, minimum=1),
        context_max_chars=_int(source, "SQLITEX_CONTEXT_MAX_CHARS", 24000, minimum=2000),
        history_turns=_int(source, "SQLITEX_HISTORY_TURNS", 3, minimum=0),
        enrich_max_tables=_int(source, "SQLITEX_ENRICH_MAX_TABLES", 200, minimum=0),
        enrich_workers=_int(source, "SQLITEX_ENRICH_WORKERS", 4, minimum=1),
        sample_rows=_int(source, "SQLITEX_SAMPLE_ROWS", 3, minimum=0),
        profile_sample_rows=_int(source, "SQLITEX_PROFILE_SAMPLE_ROWS", 500, minimum=0),
        profile_max_columns=_int(source, "SQLITEX_PROFILE_MAX_COLUMNS", 120, minimum=0),
        row_count_timeout=_float(source, "SQLITEX_ROW_COUNT_TIMEOUT", 5.0, minimum=0.1),
        debug=_bool(source, "SQLITEX_DEBUG", False) if debug is None else debug,
    )


def env_file_path() -> Path | None:
    """Return the `.env` found from the current directory, if any."""

    found = find_dotenv(usecwd=True)
    return Path(found) if found else None
