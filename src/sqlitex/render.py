"""Present results in the terminal with Rich."""

from __future__ import annotations

import re
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from sqlitex.ask import AskResult
from sqlitex.config import Settings
from sqlitex.models import Catalog, TableInfo
from sqlitex.query import QueryResult
from sqlitex.utils import human_bytes, human_number, truncate

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


def ok(message: str) -> None:
    console.print(f"[green]✓[/green] {message}")


def warn(message: str) -> None:
    console.print(f"[yellow]![/yellow] {message}")


def info(message: str) -> None:
    console.print(f"[cyan]›[/cyan] {message}")


def fail(message: str, title: str = "Error") -> None:
    err_console.print(Panel(Text(message), title=title, border_style="red", padding=(0, 1)))


def _ai_column(table: TableInfo, name: str):
    if not table.ai:
        return None
    if name in table.ai.columns:
        return table.ai.columns[name]
    lowered = name.lower()
    for key, value in table.ai.columns.items():
        if key.lower() == lowered:
            return value
    return None


def _cell(value: object) -> Text:
    if value is None:
        return Text("NULL", style="dim")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return Text(f"<blob {len(bytes(value))} B>", style="dim")
    if isinstance(value, float):
        return Text(f"{value:,.6g}")
    return Text(truncate(str(value), 120))


def print_result_table(
    columns: list[str],
    rows: list[list[object]],
    *,
    title: str | None = None,
    max_width: int = 52,
) -> None:
    if not columns:
        info("The query returned no columns.")
        return
    table = Table(
        title=title,
        box=box.SIMPLE_HEAD,
        header_style="bold cyan",
        show_lines=False,
        pad_edge=False,
        title_justify="left",
    )
    for name in columns:
        table.add_column(str(name), overflow="fold", max_width=max_width)
    for row in rows:
        table.add_row(*[_cell(value) for value in row])
    if rows:
        console.print(table)
    else:
        info("No rows.")


def print_query_result(result: QueryResult, *, title: str = "Result") -> None:
    print_result_table(result.columns, result.rows, title=title)
    suffix = f" · {result.elapsed_ms:.0f} ms"
    if result.truncated:
        suffix += " · truncated (use --max-rows to see more)"
    console.print(f"[dim]{len(result.rows)} row(s){suffix}[/dim]")


def print_overview(catalog: Catalog, *, index_hint: str | None = None) -> None:
    title = f"[bold]SQLiteX[/bold] · {catalog.database_name}"
    file_line = Text(catalog.database, style="dim")
    meta = Table.grid(padding=(0, 2))
    meta.add_column(style="dim")
    meta.add_column()
    meta.add_row("File", file_line)
    meta.add_row("Size", human_bytes(catalog.stats.size_bytes))
    meta.add_row("Generated", catalog.generated_at)
    meta.add_row("SQLite", catalog.sqlite_version or "—")
    meta.add_row(
        "Contents",
        f"{catalog.stats.tables} tables · {catalog.stats.views} "
        f"{'view' if catalog.stats.views == 1 else 'views'} · "
        f"{catalog.stats.virtual_tables} virtual · {human_number(catalog.stats.rows)} rows",
    )
    meta.add_row(
        "Relationships",
        f"{catalog.stats.relationships} · {catalog.stats.triggers} triggers",
    )
    if catalog.ai_enabled:
        meta.add_row("AI", catalog.ai_model or "configured")
    if index_hint:
        meta.add_row("Index", index_hint)

    parts: list[object] = [meta]
    if catalog.global_ai and catalog.global_ai.summary:
        parts.append(
            Panel(
                Markdown(catalog.global_ai.summary), title="What this database stores", border_style="magenta"
            )
        )
        details: list[str] = []
        if catalog.global_ai.domain:
            details.append(f"Domain: {catalog.global_ai.domain}")
        if catalog.global_ai.main_entities:
            details.append("Entities: " + ", ".join(catalog.global_ai.main_entities))
        if details:
            parts.append(Text("\n".join(details), style="dim"))
    console.print(Panel(Group(*parts), title=title, border_style="cyan", padding=(1, 2)))
    print_tables(catalog)
    if catalog.global_ai and catalog.global_ai.key_questions:
        console.print()
        console.print("[bold]Questions you can ask:[/bold]")
        for question in catalog.global_ai.key_questions[:6]:
            console.print(f"  [cyan]·[/cyan] {question}")
    if catalog.warnings:
        console.print()
        for warning in catalog.warnings:
            warn(warning)


def print_tables(catalog: Catalog) -> None:
    if not catalog.tables:
        warn("No tables or views found.")
        return
    table = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold cyan",
        show_lines=False,
        title="Tables",
        title_justify="left",
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Rows", justify="right")
    table.add_column("Cols", justify="right")
    table.add_column("FK", justify="right")
    table.add_column("Description", overflow="fold", max_width=64)
    for index, item in enumerate(catalog.tables, start=1):
        fk_count = len(item.foreign_keys)
        description = ""
        if item.ai and item.ai.summary:
            description = item.ai.summary
        elif item.ai and item.ai.purpose:
            description = item.ai.purpose
        table.add_row(
            str(index),
            Text(item.name),
            item.kind,
            human_number(item.row_count),
            str(len(item.columns)),
            str(fk_count) if fk_count else "",
            Text(truncate(description, 120)),
        )
    console.print(table)


def print_table_detail(catalog: Catalog, table: TableInfo) -> None:
    ai = table.ai
    header = Text()
    header.append(f"{table.name}\n", style="bold cyan")
    header.append(f"{table.kind} · {human_number(table.row_count)} rows · {len(table.columns)} columns")
    parts: list[object] = [header]
    if ai:
        if ai.summary:
            parts.append(Text(""))
            parts.append(Text(ai.summary))
        if ai.purpose:
            parts.append(Text(f"Purpose: {ai.purpose}", style="dim"))
        if ai.entity:
            parts.append(Text(f"A row represents: {ai.entity}", style="dim"))
        if ai.tags:
            parts.append(Text("Tags: " + ", ".join(ai.tags), style="dim"))
    console.print(Panel(Group(*parts), border_style="cyan", padding=(1, 2)))

    columns_table = Table(
        box=box.SIMPLE_HEAD, header_style="bold cyan", title="Columns", title_justify="left"
    )
    columns_table.add_column("Column", style="bold")
    columns_table.add_column("Type")
    columns_table.add_column("Constraint")
    columns_table.add_column("Nulls", justify="right")
    columns_table.add_column("Meaning", overflow="fold", max_width=56)
    columns_table.add_column("Values", overflow="fold", max_width=36)
    for column in table.columns:
        restriction = ""
        if column.primary_key:
            restriction = "PK"
        if column.foreign_key:
            restriction = (restriction + " " if restriction else "") + f"FK→{column.foreign_key}"
        nulls = ""
        if column.profile and column.profile.null_ratio is not None:
            nulls = f"{column.profile.null_ratio * 100:.0f}%"
        meaning = ""
        ai_column = _ai_column(table, column.name)
        if ai_column:
            meaning = ai_column.meaning
            if ai_column.pii:
                meaning += " (sensitive)"
        examples = ""
        if column.profile and column.profile.top_values:
            examples = ", ".join(truncate(str(item["value"]), 30) for item in column.profile.top_values[:3])
        columns_table.add_row(
            Text(column.name),
            Text(column.declared_type or "—"),
            Text(restriction),
            Text(nulls, style="dim"),
            Text(meaning),
            Text(examples, style="dim"),
        )
    console.print(columns_table)

    if table.indexes:
        index_table = Table(
            box=box.SIMPLE_HEAD, header_style="bold cyan", title="Indexes", title_justify="left"
        )
        index_table.add_column("Name")
        index_table.add_column("Unique")
        index_table.add_column("Columns")
        index_table.add_column("Origin", style="dim")
        for item in table.indexes:
            index_table.add_row(
                Text(item.name),
                "yes" if item.unique else "no",
                ", ".join(item.columns),
                item.origin or "",
            )
        console.print(index_table)

    relations = [
        relationship
        for relationship in catalog.relationships
        if table.name in {relationship.from_table, relationship.to_table}
    ]
    if relations:
        console.print("[bold]Relationships[/bold]")
        for relationship in relations:
            label = "declared" if relationship.kind == "declared" else "inferred"
            console.print(
                f"  [cyan]·[/cyan] {relationship.from_table}.{relationship.from_column} → "
                f"{relationship.to_table}.{relationship.to_column} [dim]({label})[/dim]"
            )

    if table.sample_rows:
        names = [column.name for column in table.columns if column.name in table.sample_rows[0]]
        rows = [[row.get(name) for name in names] for row in table.sample_rows]
        console.print()
        print_result_table(names, rows, title="Sample data")

    if ai and ai.example_questions:
        console.print()
        console.print("[bold]Example questions[/bold]")
        for question in ai.example_questions[:5]:
            console.print(f"  [cyan]·[/cyan] {question}")
    if ai and ai.data_quality_notes:
        console.print()
        for note in ai.data_quality_notes[:4]:
            warn(note)
    if table.notes:
        for note in table.notes:
            warn(note)


def print_answer(result: AskResult, *, show_sql: bool = True) -> None:
    if result.error:
        fail(result.error, title="Could not answer")
        if result.sql and show_sql:
            console.print(
                Panel(
                    Syntax(result.sql, "sql", theme="monokai", word_wrap=True),
                    title="Attempted SQL",
                    border_style="red",
                )
            )
        return

    console.print(
        Panel(
            Markdown(result.answer or "(no answer)"),
            title="Answer",
            border_style="green",
            padding=(1, 2),
        )
    )

    if result.mode == "sql" and result.columns:
        print_result_table(result.columns, result.rows, title="Data")

    if show_sql and result.sql:
        console.print(
            Panel(
                Syntax(result.sql, "sql", theme="monokai", word_wrap=True),
                title="Executed SQL",
                border_style="blue",
            )
        )

    footer: list[str] = []
    if result.retrieved_tables:
        footer.append("tables: " + ", ".join(result.retrieved_tables[:8]))
    footer.append(f"{result.elapsed_ms:.0f} ms")
    if result.attempts > 1:
        footer.append(f"{result.attempts} SQL attempts")
    if result.truncated:
        footer.append("truncated result")
    console.print(f"[dim]{' · '.join(footer)}[/dim]")

    if result.followups:
        console.print()
        console.print("[bold]You can continue with:[/bold]")
        for question in result.followups:
            console.print(f"  [cyan]·[/cyan] {question}")


def _mermaid_type(declared: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", declared or "TEXT").strip("_")
    return clean or "TEXT"


def _mermaid_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not clean or clean[0].isdigit():
        clean = "t_" + clean
    return clean


def build_mermaid(catalog: Catalog) -> str:
    lines = ["erDiagram"]
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for table in catalog.tables:
        safe = _mermaid_name(table.name)
        while safe in used:
            safe += "_"
        used.add(safe)
        mapping[table.name] = safe
        lines.append(f"    {safe} {{")
        seen_columns: set[str] = set()
        for column in table.columns:
            column_name = _mermaid_name(column.name)
            while column_name in seen_columns:
                column_name += "_"
            seen_columns.add(column_name)
            flags = []
            if column.primary_key:
                flags.append("PK")
            if column.foreign_key:
                flags.append("FK")
            suffix = " " + ", ".join(flags) if flags else ""
            lines.append(f"        {_mermaid_type(column.declared_type)} {column_name}{suffix}")
        lines.append("    }")

    for relationship in catalog.relationships:
        if relationship.from_table not in mapping or relationship.to_table not in mapping:
            continue
        child = mapping[relationship.from_table]
        parent = mapping[relationship.to_table]
        line = ".." if relationship.kind == "inferred" else "--"
        lines.append(f'    {parent} ||{line}o{{ {child} : "{relationship.from_column}"')
    return "\n".join(lines)


def print_mermaid(catalog: Catalog) -> None:
    console.print(Text(build_mermaid(catalog), style="cyan"), markup=False, highlight=False)


def print_catalog_status(rows: list[dict[str, str]]) -> None:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan")
    table.add_column("Database", style="bold", overflow="fold")
    table.add_column("Tables", justify="right")
    table.add_column("Relationships", justify="right")
    table.add_column("Generated")
    table.add_column("Index")
    table.add_column("Status")
    for row in rows:
        table.add_row(
            Text(row["database"]),
            row["tables"],
            row["relationships"],
            row["generated"],
            row["index"],
            Text(row["status"]),
        )
    console.print(table)


def print_config(settings: Settings, env_path: Path | None, database: Path | None) -> None:
    table = Table(
        box=box.SIMPLE_HEAD, header_style="bold cyan", title="Effective configuration", title_justify="left"
    )
    table.add_column("Variable", style="bold")
    table.add_column("Value", overflow="fold")

    key_state = "set" if settings.api_key else "(empty)"
    embed_key_state = "set" if settings.embed_api_key else "(empty)"
    rows = [
        ("SQLITEX_HOME", str(settings.home)),
        ("Database", str(database) if database else "(not set)"),
        (".env", str(env_path) if env_path else "(not found)"),
        ("SQLITEX_API_BASE_URL", settings.api_base_url),
        ("SQLITEX_API_KEY", key_state),
        ("SQLITEX_MODEL", settings.model),
        ("SQLITEX_EMBED_MODEL", settings.embed_model),
        ("SQLITEX_EMBED_BASE_URL", settings.embed_base_url),
        ("SQLITEX_EMBED_API_KEY", embed_key_state),
        ("SQLITEX_EMBEDDINGS", settings.embeddings),
        ("SQLITEX_LANGUAGE", settings.language),
        ("SQLITEX_RETRIEVAL", settings.retrieval),
        ("SQLITEX_TOP_K", str(settings.top_k)),
        ("SQLITEX_MAX_ROWS", str(settings.max_rows)),
        ("SQLITEX_QUERY_TIMEOUT", f"{settings.query_timeout:g}s"),
        ("SQLITEX_CONTEXT_MAX_CHARS", str(settings.context_max_chars)),
        ("SQLITEX_SQL_RETRIES", str(settings.sql_retries)),
        ("SQLITEX_ENRICH_WORKERS", str(settings.enrich_workers)),
        ("SQLITEX_DEBUG", "true" if settings.debug else "false"),
    ]
    for name, value in rows:
        table.add_row(Text(name), Text(value))
    console.print(table)


def print_check(label: str, status: bool | None, detail: str = "") -> None:
    if status is True:
        mark = "[green]✓[/green]"
    elif status is False:
        mark = "[red]✗[/red]"
    else:
        mark = "[yellow]![/yellow]"
    suffix = f" [dim]{detail}[/dim]" if detail else ""
    console.print(f"{mark} {label}{suffix}")
