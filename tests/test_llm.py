from __future__ import annotations

from dataclasses import replace

import pytest

from sqlitex.config import settings_from_env
from sqlitex.errors import LLMError
from sqlitex.llm import LLMClient, extract_json
from sqlitex.models import AnswerPayload


def test_extract_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_fenced_json():
    text = '```json\n{"mode": "sql", "sql": "SELECT 1"}\n```'
    assert extract_json(text) == {"mode": "sql", "sql": "SELECT 1"}


def test_extract_json_with_surrounding_text():
    assert extract_json('Sure, here it is:\n{"a": [1, 2]}\nHope it helps.') == {"a": [1, 2]}


def test_extract_array():
    assert extract_json("[1, 2, 3]") == [1, 2, 3]


def test_extract_json_ignores_invalid_prefix():
    assert extract_json('texto {no} luego {"ok": true}') == {"ok": True}


@pytest.mark.parametrize("text", ["", "   ", "sin json", "```\n```"])
def test_extract_json_fails(text):
    with pytest.raises(ValueError):
        extract_json(text)


def test_chat_json_valid(settings):
    class Canned(LLMClient):
        def __init__(self, settings, outputs):
            super().__init__(settings)
            self.outputs = list(outputs)
            self.seen = []

        def _chat_once(self, messages, *, temperature=None, label="chat"):
            self.seen.append(messages)
            return self.outputs.pop(0)

    client = Canned(settings, ['{"answer": "ok", "followups": []}'])
    payload = client.chat_json("sistema", "usuario", AnswerPayload)
    assert payload.answer == "ok"
    assert len(client.seen) == 1


def test_chat_json_repairs_invalid_json(settings):
    class Canned(LLMClient):
        def __init__(self, settings, outputs):
            super().__init__(settings)
            self.outputs = list(outputs)
            self.seen = []

        def _chat_once(self, messages, *, temperature=None, label="chat"):
            self.seen.append(list(messages))
            return self.outputs.pop(0)

    client = Canned(settings, ["esto no es json", '{"answer": "corregido"}'])
    payload = client.chat_json("sistema", "usuario", AnswerPayload)
    assert payload.answer == "corregido"
    assert len(client.seen) == 2
    assert "Validation error" in client.seen[1][-1]["content"]


def test_chat_json_gives_up(settings):
    class Canned(LLMClient):
        def _chat_once(self, messages, *, temperature=None, label="chat"):
            return "sigue sin ser json"

    client = Canned(settings)
    with pytest.raises(LLMError, match="valid JSON"):
        client.chat_json("sistema", "usuario", AnswerPayload)


def test_embed_sorts_by_index(settings):
    class Embeddings:
        def create(self, model, input):
            data = [
                type("Item", (), {"index": index, "embedding": [float(index), 1.0]})
                for index in reversed(range(len(input)))
            ]
            return type("Response", (), {"data": data})()

    vector_settings = replace(
        settings,
        embeddings="on",
        embed_model="fake-embed",
        embed_base_url="http://localhost:9/v1",
        embed_api_key="test",
    )
    client = LLMClient(vector_settings)
    client._embed_client = type("Client", (), {"embeddings": Embeddings()})()
    vectors = client.embed(["a", "b"])
    assert vectors == [[0.0, 1.0], [1.0, 1.0]]


def test_embed_is_disabled(settings):
    client = LLMClient(settings)  # embeddings=off en el fixture
    with pytest.raises(LLMError, match="disabled"):
        client.embed(["hola"])


def test_embed_empty_list(settings):
    assert LLMClient(settings).embed([]) == []


def test_json_mode_falls_back_when_unsupported(settings):
    class Completions:
        def __init__(self):
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            if "response_format" in kwargs:
                raise RuntimeError("response_format is not supported by this endpoint")
            message = type("Message", (), {"content": "hola"})()
            choice = type("Choice", (), {"message": message})()
            return type("Response", (), {"choices": [choice], "usage": None})()

    completions = Completions()
    client = LLMClient(replace(settings, json_mode=True))
    client._chat_client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    assert client.chat_text("s", "u") == "hola"
    assert completions.calls == 2
    assert client._json_mode_supported is False


def test_chat_client_authentication_error(settings):
    class Completions:
        def create(self, **kwargs):
            raise RuntimeError("401 authentication failed: invalid api key")

    client = LLMClient(settings)
    client._chat_client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    with pytest.raises(LLMError, match="Authentication"):
        client.chat_text("s", "u")


def test_list_models(settings):
    class Models:
        def list(self):
            data = [type("Model", (), {"id": "b"})(), type("Model", (), {"id": "a"})()]
            return type("Response", (), {"data": data})()

    client = LLMClient(settings)
    client._chat_client = type("Client", (), {"models": Models()})()
    assert client.list_models() == ["a", "b"]


def test_local_url_does_not_require_key():
    settings = settings_from_env(
        {"SQLITEX_API_BASE_URL": "http://localhost:1234/v1", "SQLITEX_MODEL": "m"}, home="x"
    )
    assert settings.ai_available is True
