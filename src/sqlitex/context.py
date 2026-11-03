"""Build the context sent to the model for SQL generation."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlitex.config import Settings
from sqlitex.models import Catalog, TableInfo
from sqlitex.utils import compact_json, truncate


@dataclass
class ContextBundle:
    text: str
    tables: list[str] = field(default_factory=list)
    truncated: bool = False


def _ai_column(ai, name: str):
    if name in ai.columns:
        return ai.columns[name]
    lowered = name.lower()
    for key, value in ai.columns.items():
        if key.lower() == lowered:
            return value
    return None


def _column_line(table: TableInfo, column) -> str:
    flags = []
    if column.primary_key:
        flags.append("PK")
    if column.notnull:
        flags.append("NOT NULL")
    if column.foreign_key:
        flags.append(f"FK→{column.foreign_key}")
    header = f"- {column.name}"
    if column.declared_type:
        header += f" {column.declared_type}"
    if flags:
        header += f" [{', '.join(flags)}]"
    meaning = ""
    if table.ai:
        info = _ai_column(table.ai, column.name)
        if info and info.meaning:
            meaning = f": {info.meaning}"
        if info and info.pii:
            meaning += " (sensitive data)"
    extras: list[str] = []
    if column.profile and column.profile.top_values:
        extras.append(
            "values: "
            + ", ".join(f"{item['value']} ({item['count']})" for item in column.profile.top_values[:5])
        )
    if (
        column.profile
        and column.profile.min is not None
        and column.profile.max is not None
        and column.profile.min != column.profile.max
    ):
        extras.append(f"range: {column.profile.min}…{column.profile.max}")
    suffix = f" ({'; '.join(extras)})" if extras else ""
    return f"{header}{meaning}{suffix}"


def _table_block(table: TableInfo, catalog: Catalog) -> str:
    ai = table.ai
    lines = [
        f"### Table `{table.name}` ({table.kind}, "
        f"{table.row_count if table.row_count is not None else '?'} rows)"
    ]
    if ai:
        if ai.summary:
            lines.append(f"Summary: {ai.summary}")
        if ai.purpose:
            lines.append(f"Purpose: {ai.purpose}")
        if ai.entity:
            lines.append(f"A row represents: {ai.entity}")
    lines.append("Columns:")
    lines.extend(_column_line(table, column) for column in table.columns)

    foreign = [
        f"{table.name}.{fk.column} → {fk.ref_table}.{fk.ref_column} ({fk.on_delete or 'no action'})"
        for fk in table.foreign_keys
    ]
    if foreign:
        lines.append("Foreign keys: " + "; ".join(foreign))

    if table.sql:
        lines.append("DDL:\n" + truncate(table.sql.strip(), 1800))
    if table.sample_rows:
        lines.append("Sample (truncated values):")
        for row in table.sample_rows[:2]:
            lines.append("  " + compact_json(row, 900))
    if ai and ai.data_quality_notes:
        lines.append("Notes: " + " | ".join(ai.data_quality_notes[:4]))
    return "\n".join(lines)


def _expand_tables(catalog: Catalog, names: list[str], *, max_extra: int = 4) -> list[str]:
    known = {table.name for table in catalog.tables}
    ordered = [name for name in names if name in known]
    selected = set(ordered)
    extras: list[str] = []
    for relationship in catalog.relationships:
        candidates: list[str] = []
        if relationship.from_table in selected and relationship.to_table not in selected:
            candidates.append(relationship.to_table)
        if relationship.to_table in selected and relationship.from_table not in selected:
            candidates.append(relationship.from_table)
        for candidate in candidates:
            if candidate in known and candidate not in selected and candidate not in extras:
                extras.append(candidate)
    for name in extras[:max_extra]:
        selected.add(name)
        ordered.append(name)
    return ordered


def build_context(
    catalog: Catalog,
    table_names: list[str],
    settings: Settings,
    *,
    max_chars: int | None = None,
    expand: bool = True,
) -> ContextBundle:
    """Generate a compact, prioritized context with the relevant tables."""

    limit = max_chars or settings.context_max_chars
    names = _expand_tables(catalog, table_names) if expand else list(table_names)
    by_name = {table.name: table for table in catalog.tables}
    tables = [by_name[name] for name in names if name in by_name]
    if not tables:
        return ContextBundle(text="(no tables retrieved)")

    header_lines = [f"Database: {catalog.database_name} (SQLite)", f"Tables: {len(catalog.tables)}"]
    if catalog.global_ai:
        if catalog.global_ai.summary:
            header_lines.append(f"Summary: {catalog.global_ai.summary}")
        if catalog.global_ai.domain:
            header_lines.append(f"Domain: {catalog.global_ai.domain}")
        if catalog.global_ai.main_entities:
            header_lines.append("Entities: " + ", ".join(catalog.global_ai.main_entities))
        if catalog.global_ai.notes:
            header_lines.append("Notes: " + " | ".join(catalog.global_ai.notes[:4]))

    included = {table.name for table in tables}
    relations = [
        f"- {rel.from_table}.{rel.from_column} → {rel.to_table}.{rel.to_column} "
        f"({'declared' if rel.kind == 'declared' else 'inferred, verify'})"
        for rel in catalog.relationships
        if rel.from_table in included or rel.to_table in included
    ]
    if relations:
        header_lines.append("Relevant relationships:\n" + "\n".join(relations[:25]))

    blocks = ["\n".join(header_lines)]
    length = len(blocks[0])
    truncated = False
    for table in tables:
        block = _table_block(table, catalog)
        remaining = limit - length
        if remaining <= 300:
            truncated = True
            break
        if len(block) > remaining:
            block = truncate(block, remaining)
            truncated = True
        blocks.append(block)
        length += len(block) + 2
        if truncated:
            break

    text = "\n\n".join(blocks)
    if truncated:
        text += "\n\n[Context truncated by size limit]"
    return ContextBundle(
        text=text,
        tables=[table.name for table in tables],
        truncated=truncated,
    )


def result_as_text(
    columns: list[str],
    rows: list[list[object]],
    *,
    max_rows: int = 40,
    max_chars: int = 9000,
) -> str:
    """Serialize a result for the final answer prompt."""

    header = " | ".join(columns)
    lines = [header, "-" * min(len(header), 120)]
    for row in rows[:max_rows]:
        cells = []
        for value in row:
            if value is None:
                cells.append("NULL")
            elif isinstance(value, bytes):
                cells.append(f"<blob {len(value)} bytes>")
            else:
                cells.append(truncate(str(value), 120))
        lines.append(" | ".join(cells))
    if len(rows) > max_rows:
        lines.append(f"… ({len(rows) - max_rows} more rows not included)")
    if len(rows) == 0:
        lines.append("(no rows)")
    text = "\n".join(lines)
    return truncate(text, max_chars)
