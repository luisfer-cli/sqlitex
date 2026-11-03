from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from sqlitex.config import settings_from_env
from sqlitex.demo import create_demo_database
from sqlitex.errors import LLMError
from sqlitex.introspect import introspect_database

TEST_ENV = {
    "SQLITEX_API_BASE_URL": "http://localhost:9/v1",
    "SQLITEX_API_KEY": "test-key",
    "SQLITEX_MODEL": "test-model",
    "SQLITEX_EMBEDDINGS": "off",
    "SQLITEX_SAMPLE_ROWS": "3",
    "SQLITEX_PROFILE_SAMPLE_ROWS": "100",
    "SQLITEX_LANGUAGE": "en",
    "SQLITEX_ENRICH_WORKERS": "1",
}


@pytest.fixture(autouse=True)
def isolated_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("SQLITEX_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path):
    return settings_from_env(TEST_ENV, home=tmp_path / "home")


@pytest.fixture
def demo_db(tmp_path) -> Path:
    return create_demo_database(tmp_path / "demo.sqlite")


@pytest.fixture
def catalog(demo_db, settings):
    return introspect_database(demo_db, settings)


class FakeLLM:
    """Deterministic LLM client for tests, no network."""

    available = True

    def __init__(self, responses: list[Any] | None = None, *, embed_dim: int = 8) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.embed_calls: list[list[str]] = []
        self.embed_dim = embed_dim

    def chat_json(self, system: str, user: str, schema, **kwargs):
        self.calls.append({"system": system, "user": user, "schema": schema, **kwargs})
        if not self.responses:
            raise LLMError("FakeLLM has no responses configured.")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, schema):
            return item
        return schema.model_validate(item)

    def chat_text(self, system: str, user: str, **kwargs) -> str:
        return "ok"

    def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append([digest[index] / 255 for index in range(self.embed_dim)])
        return vectors

    def list_models(self) -> list[str]:
        return ["test-model"]


@pytest.fixture
def fake_llm():
    return FakeLLM
