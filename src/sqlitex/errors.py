"""SQLiteX-specific exceptions."""

from __future__ import annotations


class SqlitexError(Exception):
    """Base error: every expected failure inherits from this class."""


class ConfigError(SqlitexError):
    """Invalid or incomplete configuration."""


class DatabaseError(SqlitexError):
    """Problem opening or reading the SQLite database."""


class CatalogError(SqlitexError):
    """Missing, corrupt, or outdated catalog."""


class LLMError(SqlitexError):
    """Error communicating with the AI provider."""


class QueryRejected(SqlitexError):
    """The SQL query is unsafe or failed to execute."""
