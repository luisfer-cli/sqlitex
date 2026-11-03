"""Client for models compatible with the OpenAI API (chat and embeddings)."""

from __future__ import annotations

import json
import re
import sys
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from sqlitex.config import Settings
from sqlitex.errors import LLMError
from sqlitex.utils import truncate

TModel = TypeVar("TModel", bound=BaseModel)


def extract_json(text: str) -> Any:
    """Extract the first JSON object/array from a model response."""

    if not text or not text.strip():
        raise ValueError("The model response is empty.")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", cleaned):
        try:
            value, _ = decoder.raw_decode(cleaned[match.start() :])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("No valid JSON object found in the response.")


def _friendly_error(exc: Exception) -> str:
    name = type(exc).__name__
    message = truncate(str(exc), 600)
    lowered = f"{name} {message}".lower()
    if "authentication" in lowered or " 401" in lowered or "api key" in lowered:
        return f"Authentication with the AI provider failed. Check SQLITEX_API_KEY. ({message})"
    if "connection" in lowered or "connect" in lowered:
        return f"Could not connect to the AI endpoint. Check SQLITEX_API_BASE_URL. ({message})"
    if "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return f"The configured model does not exist at the provider. Check SQLITEX_MODEL. ({message})"
    return f"{name}: {message}"


class LLMClient:
    """Thin wrapper around the OpenAI SDK with JSON validation and retries."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._chat_client: Any = None
        self._embed_client: Any = None
        self._json_mode_supported: bool | None = None

    @property
    def available(self) -> bool:
        return self.settings.ai_available

    def _build_client(self, base_url: str, api_key: str) -> Any:
        from openai import OpenAI

        return OpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",
            timeout=self.settings.timeout,
            max_retries=self.settings.max_retries,
            default_headers=self.settings.extra_headers or None,
        )

    @property
    def chat_client(self) -> Any:
        if self._chat_client is None:
            self._chat_client = self._build_client(self.settings.api_base_url, self.settings.api_key)
        return self._chat_client

    @property
    def embed_client(self) -> Any:
        if self._embed_client is None:
            self._embed_client = self._build_client(self.settings.embed_base_url, self.settings.embed_api_key)
        return self._embed_client

    def _log_usage(self, response: Any, label: str) -> None:
        if not self.settings.debug:
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", "?")
        completion = getattr(usage, "completion_tokens", "?")
        sys.stderr.write(f"[sqlitex:{label}] tokens entrada={prompt} salida={completion}\n")

    def _chat_once(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        label: str = "chat",
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature if temperature is None else temperature,
        }
        use_json_mode = self.settings.json_mode and self._json_mode_supported is not False
        json_sent = use_json_mode
        if use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self.chat_client.chat.completions.create(**kwargs)
        except Exception as exc:
            if use_json_mode and "response_format" in str(exc).lower():
                # Some compatible endpoints do not support JSON mode: retry without it.
                self._json_mode_supported = False
                json_sent = False
                kwargs.pop("response_format", None)
                try:
                    response = self.chat_client.chat.completions.create(**kwargs)
                except Exception as retry_exc:
                    raise LLMError(_friendly_error(retry_exc)) from retry_exc
            else:
                raise LLMError(_friendly_error(exc)) from exc

        if json_sent:
            self._json_mode_supported = True
        self._log_usage(response, label)
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise LLMError("The provider returned a response without content.") from exc
        if not content:
            raise LLMError("The model returned an empty response.")
        return str(content)

    def chat_text(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        label: str = "chat",
    ) -> str:
        return self._chat_once(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature,
            label=label,
        )

    def chat_json(
        self,
        system: str,
        user: str,
        schema: type[TModel],
        *,
        temperature: float | None = None,
        max_repairs: int = 1,
        label: str = "json",
    ) -> TModel:
        """Request JSON, validate it with Pydantic, and repair one invalid response."""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error = ""
        for attempt in range(max_repairs + 1):
            raw = self._chat_once(messages, temperature=temperature, label=label)
            try:
                return schema.model_validate(extract_json(raw))
            except (ValueError, ValidationError) as exc:
                last_error = truncate(str(exc), 800)
                if attempt >= max_repairs:
                    break
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response does not match the requested format. "
                            "Return ONLY the corrected JSON object, with no extra text, "
                            "no code blocks, and no comments.\n"
                            f"Validation error: {last_error}"
                        ),
                    }
                )
        raise LLMError(f"The model did not return valid JSON after several attempts. {last_error}")

    def embed(self, texts: list[str], *, batch_size: int = 64) -> list[list[float]]:
        if not texts:
            return []
        if not self.settings.embeddings_available:
            raise LLMError("Embeddings are disabled or not configured.")

        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            try:
                response = self.embed_client.embeddings.create(model=self.settings.embed_model, input=batch)
            except Exception as exc:
                raise LLMError(_friendly_error(exc)) from exc
            items = sorted(response.data, key=lambda item: item.index)
            if len(items) != len(batch):
                raise LLMError(f"The endpoint returned {len(items)} embeddings for {len(batch)} texts.")
            vectors.extend([list(item.embedding) for item in items])
        return vectors

    def list_models(self) -> list[str]:
        try:
            response = self.chat_client.models.list()
        except Exception as exc:
            raise LLMError(_friendly_error(exc)) from exc
        return sorted({model.id for model in response.data})

    def check_embeddings(self) -> int:
        vectors = self.embed(["embedding test"])
        if not vectors or not vectors[0]:
            raise LLMError("The embeddings endpoint returned an empty vector.")
        return len(vectors[0])
