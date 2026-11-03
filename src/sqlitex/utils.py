"""Utilidades compartidas."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from typing import Any


def quote_ident(name: str) -> str:
    """Escape a SQLite identifier with double quotes."""

    return '"' + str(name).replace('"', '""') + '"'


def jsonable(value: Any) -> Any:
    """Convert a SQLite value into something JSON-serializable."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<blob {len(bytes(value))} bytes>"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def human_number(value: int | float | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}".replace(",", ".")


def human_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_words(text: str) -> str:
    """Lowercase, strip accents, and remove special characters."""

    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return ascii_text.lower()


def safe_slug(value: str, max_length: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize_words(value)).strip("-")
    return (slug or "db")[:max_length].strip("-")


def compact_json(value: Any, limit: int = 1200) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return truncate(text, limit)


def language_name(code: str) -> str:
    return {
        "es": "Spanish",
        "en": "English",
        "pt": "Portuguese",
        "fr": "French",
        "de": "German",
        "it": "Italian",
    }.get(code.lower(), code)
