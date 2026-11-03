"""SQLite database introspection.

Extracts schema, relationships, indexes, triggers, row counts, sample rows, and
approximate statistical profiles for columns.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import quote

from sqlitex.config import Settings
from sqlitex.errors import DatabaseError
from sqlitex.models import (
    Catalog,
    CatalogStats,
    ColumnInfo,
    ForeignKeyInfo,
    IndexInfo,
    Relationship,
    TableInfo,
    TriggerInfo,
    ValueProfile,
)
from sqlitex.utils import jsonable, normalize_words, quote_ident, sha256_text, truncate

T = TypeVar("T")
_TIMEOUT = object()

_FTS_SUFFIXES = {"data", "idx", "content", "docsize", "config", "segments", "segdir", "stat"}
_RTREE_SUFFIXES = {"node", "parent", "rowid"}
_MAX_SAMPLE_VALUE = 400


def connect_readonly(path: Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open a strictly read-only SQLite connection."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise DatabaseError(f"Database file does not exist: {resolved}")
    uri = "file:" + quote(str(resolved), safe="/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.text_factory = lambda raw: raw.decode("utf-8", "replace")
        conn.execute("PRAGMA query_only = ON")
    except sqlite3.Error as exc:
        raise DatabaseError(f"Could not open database {resolved}: {exc}") from exc
    return conn


def _timed(conn: sqlite3.Connection, seconds: float, fn: Callable[[], T]) -> T | object:
    deadline = time.monotonic() + seconds
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 5000)
    try:
        return fn()
    except sqlite3.OperationalError as exc:
        if "interrupt" in str(exc).lower():
            return _TIMEOUT
        raise
    finally:
        conn.set_progress_handler(None, 0)


def _pragma_rows(conn: sqlite3.Connection, name: str, argument: str) -> list[sqlite3.Row]:
    try:
        return conn.execute(f"SELECT * FROM pragma_{name}(?)", (argument,)).fetchall()
    except sqlite3.Error:
        return []


def _columns(conn: sqlite3.Connection, table: str) -> list[ColumnInfo]:
    columns: list[ColumnInfo] = []
    for row in _pragma_rows(conn, "table_info", table):
        columns.append(
            ColumnInfo(
                name=row["name"],
                declared_type=(row["type"] or "").strip(),
                notnull=bool(row["notnull"]),
                default=row["dflt_value"],
                primary_key=bool(row["pk"]),
            )
        )
    return columns


def _foreign_keys(conn: sqlite3.Connection, table: str) -> list[ForeignKeyInfo]:
    result: list[ForeignKeyInfo] = []
    for row in _pragma_rows(conn, "foreign_key_list", table):
        result.append(
            ForeignKeyInfo(
                column=row["from"],
                ref_table=row["table"],
                ref_column=row["to"] or "",
                on_update=row["on_update"],
                on_delete=row["on_delete"],
                group=row["id"],
            )
        )
    return result


def _indexes(conn: sqlite3.Connection, table: str, index_sql: dict[str, str]) -> list[IndexInfo]:
    result: list[IndexInfo] = []
    for row in _pragma_rows(conn, "index_list", table):
        columns = [
            col["name"] for col in _pragma_rows(conn, "index_info", row["name"]) if col["name"] is not None
        ]
        result.append(
            IndexInfo(
                name=row["name"],
                unique=bool(row["unique"]),
                origin=row["origin"],
                partial=bool(row["partial"]),
                columns=columns,
                sql=index_sql.get(row["name"]),
            )
        )
    return result


def _row_count(conn: sqlite3.Connection, table: str, timeout: float) -> int | None:
    try:
        value = _timed(
            conn,
            timeout,
            lambda: conn.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}").fetchone()[0],
        )
    except sqlite3.Error:
        return None
    return None if value is _TIMEOUT else int(value)


def _samples(conn: sqlite3.Connection, table: str, limit: int) -> tuple[list[dict[str, Any]], str | None]:
    if limit <= 0:
        return [], None
    try:
        cursor = conn.execute(f"SELECT * FROM {quote_ident(table)} LIMIT {int(limit)}")
        names = [description[0] for description in cursor.description or []]
        rows: list[dict[str, Any]] = []
        for raw in cursor.fetchall():
            row: dict[str, Any] = {}
            for name, value in zip(names, raw, strict=False):
                if isinstance(value, str):
                    row[name] = truncate(value, _MAX_SAMPLE_VALUE)
                else:
                    row[name] = jsonable(value)
            rows.append(row)
        return rows, None
    except sqlite3.Error as exc:
        return [], f"Could not read sample rows: {exc}"


def _profile_column(
    conn: sqlite3.Connection,
    table: str,
    column: ColumnInfo,
    sample_size: int,
) -> ValueProfile | None:
    if sample_size <= 0:
        return None
    qc = quote_ident(column.name)
    qt = quote_ident(table)
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n, COUNT({qc}) AS nonnull, COUNT(DISTINCT {qc}) AS distinct_n, "
            f"MIN({qc}) AS min_v, MAX({qc}) AS max_v "
            f"FROM (SELECT {qc} FROM {qt} LIMIT {int(sample_size)})"
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None or not row["n"]:
        return ValueProfile(sample_size=0)

    total = int(row["n"])
    non_null = int(row["nonnull"] or 0)
    distinct = int(row["distinct_n"] or 0)
    profile = ValueProfile(
        sample_size=total,
        non_null=non_null,
        null_ratio=round(1 - (non_null / total if total else 0), 3),
        distinct=distinct,
        min=jsonable(row["min_v"]),
        max=jsonable(row["max_v"]),
    )

    if 1 <= distinct <= 25 and total >= 5:
        try:
            values = conn.execute(
                f"SELECT {qc} AS value, COUNT(*) AS n "
                f"FROM (SELECT {qc} FROM {qt} LIMIT {int(sample_size)}) "
                f"WHERE {qc} IS NOT NULL GROUP BY {qc} ORDER BY n DESC, value LIMIT 5"
            ).fetchall()
            profile.top_values = [
                {"value": jsonable(item["value"]), "count": int(item["n"])} for item in values
            ]
        except sqlite3.Error:
            pass
    return profile


def _virtual_module(sql: str | None) -> str | None:
    if not sql:
        return None
    match = re.search(r"\bUSING\s+([A-Za-z0-9_]+)", sql, re.IGNORECASE)
    return match.group(1).lower() if match else None


def _shadow_tables(virtual: dict[str, str], all_names: set[str]) -> set[str]:
    shadows: set[str] = set()
    for parent, module in virtual.items():
        if module.startswith("fts"):
            suffixes = _FTS_SUFFIXES
        elif module == "rtree":
            suffixes = _RTREE_SUFFIXES
        else:
            continue
        for suffix in suffixes:
            candidate = f"{parent}_{suffix}"
            if candidate in all_names:
                shadows.add(candidate)
    return shadows


def _singular_forms(word: str) -> set[str]:
    forms = {word}
    if word.endswith("es") and len(word) > 3:
        forms.add(word[:-2])
    if word.endswith("s") and len(word) > 2:
        forms.add(word[:-1])
    return forms


def _infer_relationships(tables: list[TableInfo], declared: list[Relationship]) -> list[Relationship]:
    """Suggest foreign keys from columns named like `<table>_id`."""

    known = {(rel.from_table, rel.from_column, rel.to_table, rel.to_column) for rel in declared}
    by_stem: dict[str, list[tuple[str, list[str]]]] = {}
    for table in tables:
        if table.kind != "table":
            continue
        pks = [column.name for column in table.columns if column.primary_key]
        if not pks:
            continue
        for form in _singular_forms(normalize_words(table.name)):
            cleaned = re.sub(r"[^a-z0-9]", "", form)
            if cleaned:
                by_stem.setdefault(cleaned, []).append((table.name, pks))

    inferred: list[Relationship] = []
    for table in tables:
        if table.kind != "table":
            continue
        existing_columns = {fk.column for fk in table.foreign_keys}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            normalized = re.sub(r"[^a-z0-9]", "", normalize_words(column.name))
            if not normalized.endswith("id") or normalized == "id":
                continue
            stem = normalized[:-2]
            candidates = by_stem.get(stem, [])
            if not candidates:
                continue
            target_table, pks = candidates[0]
            if target_table == table.name:
                continue
            relationship = Relationship(
                from_table=table.name,
                from_column=column.name,
                to_table=target_table,
                to_column=pks[0],
                kind="inferred",
                description="Relationship inferred from the column name (no declared FK).",
            )
            key = (
                relationship.from_table,
                relationship.from_column,
                relationship.to_table,
                relationship.to_column,
            )
            if key not in known:
                known.add(key)
                inferred.append(relationship)
    return inferred


def _build_relationships(tables: list[TableInfo]) -> list[Relationship]:
    by_name = {table.name: table for table in tables}
    declared: list[Relationship] = []
    seen: set[tuple[str, str, str, str]] = set()

    # Fill in declared FKs without a target column (they reference the parent PK).
    for table in tables:
        for fk in table.foreign_keys:
            if fk.ref_column:
                continue
            target = by_name.get(fk.ref_table)
            if target:
                pks = [column.name for column in target.columns if column.primary_key]
                if pks:
                    fk.ref_column = pks[0]

    for table in tables:
        for fk in table.foreign_keys:
            key = (table.name, fk.column, fk.ref_table, fk.ref_column)
            if key in seen:
                continue
            seen.add(key)
            declared.append(
                Relationship(
                    from_table=table.name,
                    from_column=fk.column,
                    to_table=fk.ref_table,
                    to_column=fk.ref_column,
                    kind="declared",
                )
            )

    for table in tables:
        for fk in table.foreign_keys:
            for column in table.columns:
                if column.name == fk.column:
                    column.foreign_key = f"{fk.ref_table}.{fk.ref_column}"

    return declared + _infer_relationships(tables, declared)


def _pragmas(conn: sqlite3.Connection) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in (
        "page_size",
        "page_count",
        "encoding",
        "journal_mode",
        "user_version",
        "application_id",
        "auto_vacuum",
        "foreign_keys",
    ):
        try:
            row = conn.execute(f"PRAGMA {name}").fetchone()
            result[name] = jsonable(row[0]) if row else None
        except sqlite3.Error:
            continue
    return result


def introspect_database(
    db_path: Path,
    settings: Settings,
    *,
    include_profiles: bool = True,
) -> Catalog:
    """Analyze the database and build a catalog without AI annotations."""

    db_path = db_path.expanduser().resolve()
    conn = connect_readonly(db_path)
    warnings: list[str] = []
    try:
        sqlite_version = conn.execute("SELECT sqlite_version()").fetchone()[0]
        pragmas = _pragmas(conn)

        master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE type IN ('table','view','trigger') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'view' THEN 1 ELSE 2 END, name"
        ).fetchall()

        all_table_names = {row["name"] for row in master if row["type"] in {"table", "view"}}
        virtual: dict[str, str] = {}
        for row in master:
            if row["type"] == "table" and row["sql"]:
                module = _virtual_module(row["sql"])
                if module:
                    virtual[row["name"]] = module
        shadows = _shadow_tables(virtual, all_table_names)

        index_sql: dict[str, str] = {
            row["name"]: row["sql"]
            for row in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            ).fetchall()
        }

        tables: list[TableInfo] = []
        triggers: list[TriggerInfo] = []
        profiled_columns = 0

        for row in master:
            name = row["name"]
            master_type = row["type"]
            if master_type == "trigger":
                triggers.append(TriggerInfo(name=name, table=row["tbl_name"], sql=row["sql"]))
                continue
            if name in shadows:
                continue

            if master_type == "view":
                kind = "view"
            elif name in virtual:
                kind = "virtual"
            else:
                kind = "table"

            columns = _columns(conn, name)
            if not columns:
                warnings.append(f"Table/view “{name}” does not expose readable columns.")

            table = TableInfo(
                name=name,
                kind=kind,  # type: ignore[arg-type]
                sql=row["sql"],
                columns=columns,
                foreign_keys=_foreign_keys(conn, name),
                indexes=_indexes(conn, name, index_sql),
                row_count=_row_count(conn, name, settings.row_count_timeout),
                schema_hash=sha256_text(
                    json.dumps(
                        {
                            "sql": row["sql"],
                            "columns": [
                                [column.name, column.declared_type, column.notnull, column.primary_key]
                                for column in columns
                            ],
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    )
                ),
            )
            if table.row_count is None:
                table.notes.append("Could not compute row count (large or locked table).")

            samples, sample_error = _samples(conn, name, settings.sample_rows)
            table.sample_rows = samples
            if sample_error:
                table.notes.append(sample_error)

            if include_profiles and table.row_count != 0 and columns:
                sample_size = settings.profile_sample_rows
                if table.row_count is not None:
                    sample_size = min(sample_size, table.row_count)
                if sample_size > 0:
                    remaining = settings.profile_max_columns - profiled_columns
                    eligible = columns[: max(0, remaining)]
                    for column in eligible:
                        profile = _profile_column(conn, name, column, sample_size)
                        if profile is not None:
                            column.profile = profile
                    profiled_columns += len(eligible)
                    if len(eligible) < len(columns):
                        table.notes.append(f"Profile limited to {len(eligible)} columns for performance.")

            tables.append(table)

        relationships = _build_relationships(tables)

        known_rows = [
            table.row_count
            for table in tables
            if table.row_count is not None and table.kind in {"table", "virtual"}
        ]
        stats = CatalogStats(
            tables=sum(1 for table in tables if table.kind == "table"),
            views=sum(1 for table in tables if table.kind == "view"),
            virtual_tables=sum(1 for table in tables if table.kind == "virtual"),
            rows=sum(known_rows) if known_rows else None,
            relationships=len(relationships),
            triggers=len(triggers),
            size_bytes=db_path.stat().st_size,
        )

        fingerprint = sha256_text(
            json.dumps(
                {
                    "tables": sorted([[table.name, table.schema_hash] for table in tables]),
                    "relationships": sorted(
                        [
                            [rel.from_table, rel.from_column, rel.to_table, rel.to_column]
                            for rel in relationships
                        ]
                    ),
                },
                ensure_ascii=False,
            )
        )

        return Catalog(
            database=str(db_path),
            database_name=db_path.stem,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            sqlite_version=sqlite_version,
            stats=stats,
            pragmas=pragmas,
            tables=tables,
            relationships=relationships,
            triggers=triggers,
            warnings=warnings,
            language=settings.language,
            fingerprint=fingerprint,
        )
    except sqlite3.Error as exc:
        raise DatabaseError(f"Error reading the schema of {db_path}: {exc}") from exc
    finally:
        conn.close()
