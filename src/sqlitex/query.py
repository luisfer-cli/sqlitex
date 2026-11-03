"""Execute SQL strictly in read-only mode."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlitex.errors import QueryRejected
from sqlitex.introspect import connect_readonly
from sqlitex.utils import jsonable

_ALLOWED_START = {"select", "with", "explain"}
_DENIED_ACTIONS = {
    "SQLITE_INSERT",
    "SQLITE_UPDATE",
    "SQLITE_DELETE",
    "SQLITE_CREATE_INDEX",
    "SQLITE_CREATE_TABLE",
    "SQLITE_CREATE_TEMP_INDEX",
    "SQLITE_CREATE_TEMP_TABLE",
    "SQLITE_CREATE_TEMP_TRIGGER",
    "SQLITE_CREATE_TEMP_VIEW",
    "SQLITE_CREATE_TRIGGER",
    "SQLITE_CREATE_VIEW",
    "SQLITE_DROP_INDEX",
    "SQLITE_DROP_TABLE",
    "SQLITE_DROP_TEMP_INDEX",
    "SQLITE_DROP_TEMP_TABLE",
    "SQLITE_DROP_TEMP_TRIGGER",
    "SQLITE_DROP_TEMP_VIEW",
    "SQLITE_DROP_TRIGGER",
    "SQLITE_DROP_VIEW",
    "SQLITE_ALTER_TABLE",
    "SQLITE_REINDEX",
    "SQLITE_ANALYZE",
    "SQLITE_ATTACH",
    "SQLITE_DETACH",
    "SQLITE_PRAGMA",
    "SQLITE_VACUUM",
    "SQLITE_SAVEPOINT",
}
_DENIED_CODES = {getattr(sqlite3, name) for name in _DENIED_ACTIONS if hasattr(sqlite3, name)}

# FTS5 internally runs `PRAGMA data_version` during a read. We also allow other
# introspection pragmas that do not modify the database.
_SAFE_PRAGMAS = {
    "data_version",
    "table_info",
    "table_xinfo",
    "table_list",
    "index_info",
    "index_list",
    "foreign_key_list",
    "database_list",
    "collation_list",
    "compile_options",
    "function_list",
    "module_list",
    "pragma_list",
    "freelist_count",
    "page_count",
    "page_size",
    "encoding",
    "journal_mode",
    "schema_version",
}


@dataclass
class QueryResult:
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    elapsed_ms: float = 0.0

    def jsonable_rows(self) -> list[list[Any]]:
        return [[jsonable(value) for value in row] for row in self.rows]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.jsonable_rows(),
            "truncated": self.truncated,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


def _leading_keyword(sql: str) -> str:
    text = sql.lstrip()
    while True:
        if text.startswith("--"):
            newline = text.find("\n")
            if newline == -1:
                return ""
            text = text[newline + 1 :].lstrip()
            continue
        if text.startswith("/*"):
            end = text.find("*/")
            if end == -1:
                return ""
            text = text[end + 2 :].lstrip()
            continue
        break
    match = re.match(r"([A-Za-z]+)", text)
    return match.group(1).lower() if match else ""


def validate_readonly_sql(sql: str) -> str:
    """Validate that this is a single read query and return it cleaned up."""

    cleaned = (sql or "").strip()
    while cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if not cleaned:
        raise QueryRejected("The SQL query is empty.")

    keyword = _leading_keyword(cleaned)
    if keyword not in _ALLOWED_START:
        raise QueryRejected(
            "Only read-only queries are allowed (SELECT, WITH, or EXPLAIN). "
            f"The query starts with “{keyword or '?'}”."
        )
    return cleaned


def _install_authorizer(conn: sqlite3.Connection) -> None:
    def authorizer(action: int, arg1, arg2, dbname, trigger):
        if action == sqlite3.SQLITE_PRAGMA:
            pragma = str(arg1 or "").lower()
            # Reads only: a pragma with an assigned value could modify state.
            allowed = pragma in _SAFE_PRAGMAS and arg2 is None
            return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY if action in _DENIED_CODES else sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)


def _wrap_with_limit(sql: str, max_rows: int) -> str:
    if _leading_keyword(sql) == "explain":
        return sql
    return f"SELECT * FROM (\n{sql}\n) LIMIT {int(max_rows) + 1}"


def run_readonly_query(
    db_path: Path,
    sql: str,
    *,
    max_rows: int = 50,
    timeout: float = 10.0,
) -> QueryResult:
    cleaned = validate_readonly_sql(sql)
    if max_rows < 1:
        raise QueryRejected("max_rows must be greater than zero.")

    conn = connect_readonly(db_path)
    deadline = time.monotonic() + max(0.1, timeout)
    try:
        _install_authorizer(conn)
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 2000)
        start = time.perf_counter()
        try:
            cursor = conn.execute(_wrap_with_limit(cleaned, max_rows))
            columns = [description[0] for description in cursor.description or []]
            fetched = cursor.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise QueryRejected(f"The query exceeded the {timeout:.0f}s time limit.") from exc
            raise QueryRejected(f"Error executing SQL: {exc}") from exc
        except sqlite3.Error as exc:
            raise QueryRejected(f"Error executing SQL: {exc}") from exc
        elapsed_ms = (time.perf_counter() - start) * 1000
    finally:
        conn.close()

    truncated = len(fetched) > max_rows
    rows = [list(row) for row in fetched[:max_rows]]
    return QueryResult(
        sql=cleaned,
        columns=columns,
        rows=rows,
        truncated=truncated,
        elapsed_ms=elapsed_ms,
    )
