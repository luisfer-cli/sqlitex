"""SQLiteX command-line interface."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from sqlitex import __version__, render
from sqlitex.ask import HistoryTurn, answer_question
from sqlitex.catalog import (
    index_paths,
    iter_catalog_files,
    load_catalog,
    load_catalog_for_db,
    resolve_catalog,
    resolve_db_path,
    save_catalog,
)
from sqlitex.config import Settings, env_file_path, settings_from_env
from sqlitex.demo import create_demo_database
from sqlitex.enrich import EnrichReport, enrich_catalog
from sqlitex.errors import CatalogError, ConfigError, DatabaseError, SqlitexError
from sqlitex.index import IndexData, build_index, index_summary, load_index
from sqlitex.introspect import introspect_database
from sqlitex.llm import LLMClient
from sqlitex.query import run_readonly_query
from sqlitex.utils import truncate

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Explore a SQLite database and query it in natural language.\n\n"
        '[bold]Typical flow:[/bold] sqlitex init data.sqlite → sqlitex ask "how much did we sell?"'
    ),
)


@dataclass
class AppState:
    settings: Settings


def _state(ctx: typer.Context) -> AppState:
    state = ctx.obj
    if not isinstance(state, AppState):
        state = AppState(settings=settings_from_env())
        ctx.obj = state
    return state


@app.callback()
def main(
    ctx: typer.Context,
    debug: Annotated[bool, typer.Option("--debug", help="Show tokens and internal details.")] = False,
    home: Annotated[
        Path | None,
        typer.Option("--home", help="Catalog and index directory (default: .sqlitex)."),
    ] = None,
) -> None:
    """Configure the application before running a command."""

    try:
        settings = settings_from_env(home=home, debug=True if debug else None)
    except ConfigError as exc:
        render.fail(str(exc), title="Invalid configuration")
        raise typer.Exit(1) from exc
    ctx.obj = AppState(settings=settings)


def guard(func: Callable[..., Any]) -> Callable[..., Any]:
    """Turn domain errors into readable messages and exit codes."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except SqlitexError as exc:
            render.fail(str(exc))
            raise typer.Exit(1) from exc
        except KeyboardInterrupt:
            render.warn("Interrumpido.")
            raise typer.Exit(130) from None

    return wrapper


def _emit_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _client_or_fail(settings: Settings) -> LLMClient:
    client = LLMClient(settings)
    if not client.available:
        raise ConfigError(
            "The AI is not configured. Copy .env.example to .env and set "
            "SQLITEX_API_BASE_URL, SQLITEX_API_KEY, and SQLITEX_MODEL."
        )
    return client


def _ensure_index(
    catalog_path: Path,
    catalog,
    settings: Settings,
    client: LLMClient,
    *,
    use_embeddings: bool = True,
    quiet: bool = False,
) -> IndexData:
    index = load_index(catalog_path, catalog, settings)
    if index is not None:
        return index
    if not quiet:
        render.info("No RAG index found; building it…")
        with render.console.status("Indexing the catalog…"):
            return build_index(
                catalog,
                catalog_path,
                settings,
                client,
                use_embeddings=use_embeddings,
                on_message=render.warn,
            )
    return build_index(
        catalog,
        catalog_path,
        settings,
        client,
        use_embeddings=use_embeddings,
        on_message=None,
    )


def _find_table(catalog, name: str):
    lowered = name.lower()
    for table in catalog.tables:
        if table.name.lower() == lowered:
            return table
    candidates = [table.name for table in catalog.tables if lowered in table.name.lower()]
    hint = f" Similar: {', '.join(candidates[:5])}." if candidates else ""
    raise CatalogError(f"Table “{name}” does not exist.{hint}")


def _optional_database(settings: Settings) -> Path | None:
    if settings.db is not None:
        return settings.db
    files = iter_catalog_files(settings)
    if len(files) == 1:
        try:
            return Path(load_catalog(files[0]).database)
        except CatalogError:
            return None
    return None


@app.command()
@guard
def init(
    ctx: typer.Context,
    database: Annotated[Path, typer.Argument(help="Path to the SQLite file (.db, .sqlite, .sqlite3).")],
    no_ai: Annotated[
        bool,
        typer.Option("--no-ai", help="Introspection only: no model calls and no embeddings."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-annotate every table with AI (ignore cache)."),
    ] = False,
    no_profile: Annotated[
        bool,
        typer.Option("--no-profile", help="Skip column profiles (faster)."),
    ] = False,
    lexical: Annotated[
        bool,
        typer.Option("--lexical", help="Do not generate embeddings; use a lexical (BM25) index."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output.")] = False,
) -> None:
    """Analyze a SQLite database and build its semantic catalog + RAG index."""

    settings = _state(ctx).settings
    db_path = database.expanduser().resolve()
    if not db_path.is_file():
        raise DatabaseError(f"Database file does not exist: {db_path}")

    if not json_output:
        render.info(f"Inspecting [bold]{db_path}[/bold]…")
    with render.console.status("Reading schema, relationships, and samples…"):
        catalog = introspect_database(db_path, settings, include_profiles=not no_profile)

    previous = None
    try:
        previous = load_catalog_for_db(db_path, settings)
    except CatalogError:
        previous = None

    client = LLMClient(settings)
    report = EnrichReport()
    if no_ai:
        if not json_output:
            render.info("--no-ai mode: skipping semantic annotation.")
    elif not client.available:
        if not json_output:
            render.warn(
                "AI not configured: the catalog will contain structural information only. "
                "Copy .env.example to .env to enable it."
            )
    else:
        if json_output:
            report = enrich_catalog(
                catalog,
                settings,
                client,
                force=force,
                previous=previous,
            )
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                console=render.console,
                transient=True,
            ) as progress:
                task = progress.add_task("Analyzing with AI…", total=1)

                def on_progress(done: int, total: int, label: str) -> None:
                    progress.update(
                        task,
                        completed=done,
                        total=max(total, 1),
                        description=truncate("AI · " + label, 70),
                    )

                report = enrich_catalog(
                    catalog,
                    settings,
                    client,
                    force=force,
                    previous=previous,
                    on_progress=on_progress,
                    on_message=render.warn,
                )

    catalog_path = save_catalog(catalog, settings)
    use_embeddings = not no_ai and not lexical
    index_warnings: list[str] = []
    if json_output:
        index = build_index(
            catalog,
            catalog_path,
            settings,
            client,
            use_embeddings=use_embeddings,
            on_message=index_warnings.append,
        )
    else:
        with render.console.status("Building the RAG index…"):
            index = build_index(
                catalog,
                catalog_path,
                settings,
                client,
                use_embeddings=use_embeddings,
                on_message=render.warn,
            )

    if report.failed and not json_output:
        for failure in report.failed:
            render.warn(f"{failure['table']}: {failure['error']}")

    if json_output:
        _emit_json(
            {
                "database": str(db_path),
                "catalog": str(catalog_path),
                "tables": len(catalog.tables),
                "relationships": len(catalog.relationships),
                "rows": catalog.stats.rows,
                "ai": {
                    "enabled": report.enabled,
                    "enriched": report.enriched,
                    "reused": report.reused,
                    "failed": report.failed,
                },
                "index": {
                    "documents": len(index.docs),
                    "vectors": index.vector_ready,
                    "model": index.model,
                    "warnings": index_warnings,
                },
                "warnings": catalog.warnings,
            }
        )
        return

    render.print_overview(catalog, index_hint=index_summary(index))
    render.console.print()
    render.ok(f"Catalog saved to [dim]{catalog_path}[/dim]")
    if report.enriched:
        render.ok(f"{report.enriched} tables annotated with AI ({report.reused} reused).")
    render.info('Try now: [bold]sqlitex ask "what information does this database store?"[/bold]')


@app.command()
@guard
def ask(
    ctx: typer.Context,
    question: Annotated[list[str], typer.Argument(help="Question in natural language.")],
    db: Annotated[Path | None, typer.Option("--db", "-d", help="Database to query.")] = None,
    max_rows: Annotated[int | None, typer.Option("--max-rows", "-n", help="Maximum rows.")] = None,
    top_k: Annotated[int | None, typer.Option("--top-k", help="Tables to retrieve for context.")] = None,
    retrieval: Annotated[
        str | None,
        typer.Option("--retrieval", help="hybrid | lexical | vector | off"),
    ] = None,
    show_sql: Annotated[bool, typer.Option("--sql/--no-sql", help="Show the executed SQL.")] = True,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output.")] = False,
) -> None:
    """Ask a question about the data (RAG + SQL generation)."""

    settings = _state(ctx).settings
    text = " ".join(question).strip()
    if not text:
        raise ConfigError('Write a question, for example: sqlitex ask "how many customers are there?"')

    if max_rows is not None:
        settings = replace(settings, max_rows=max_rows)
    if top_k is not None:
        settings = replace(settings, top_k=top_k)
    if retrieval is not None:
        if retrieval not in {"hybrid", "lexical", "vector", "off"}:
            raise ConfigError("--retrieval must be hybrid, lexical, vector, or off.")
        settings = replace(settings, retrieval=retrieval)

    catalog_path, catalog = resolve_catalog(settings, db)
    db_path = Path(catalog.database)
    if not db_path.is_file():
        raise DatabaseError(f"The database referenced by the catalog no longer exists: {db_path}")

    client = _client_or_fail(settings)
    index = _ensure_index(catalog_path, catalog, settings, client, quiet=json_output)

    if json_output:
        result = answer_question(
            text,
            catalog=catalog,
            index=index,
            db_path=db_path,
            client=client,
            settings=settings,
            max_rows=settings.max_rows,
        )
    else:
        with render.console.status("Thinking…") as status:
            result = answer_question(
                text,
                catalog=catalog,
                index=index,
                db_path=db_path,
                client=client,
                settings=settings,
                max_rows=settings.max_rows,
                on_step=lambda message: status.update(f"[cyan]{message}[/cyan]"),
            )

    if json_output:
        _emit_json(result.to_dict())
    else:
        render.print_answer(result, show_sql=show_sql)
    if result.error:
        raise typer.Exit(1)


@app.command()
@guard
def chat(
    ctx: typer.Context,
    db: Annotated[Path | None, typer.Option("--db", "-d", help="Database to query.")] = None,
    max_rows: Annotated[int | None, typer.Option("--max-rows", "-n", help="Maximum rows.")] = None,
    show_sql: Annotated[bool, typer.Option("--sql/--no-sql", help="Show SQL in each answer.")] = False,
) -> None:
    """Interactive conversation with the database."""

    settings = _state(ctx).settings
    if max_rows is not None:
        settings = replace(settings, max_rows=max_rows)

    catalog_path, catalog = resolve_catalog(settings, db)
    db_path = Path(catalog.database)
    if not db_path.is_file():
        raise DatabaseError(f"The database referenced by the catalog no longer exists: {db_path}")

    client = _client_or_fail(settings)
    index = _ensure_index(catalog_path, catalog, settings, client)

    render.console.print(
        render.Panel(
            render.Text(
                f"{catalog.database_name} · {len(catalog.tables)} tables · model {settings.model}\n"
                "Type your question or /help for commands. Ctrl+D to exit."
            ),
            title="SQLiteX chat",
            border_style="cyan",
        )
    )

    history: list[HistoryTurn] = []
    while True:
        try:
            line = render.console.input("\n[bold cyan]you ›[/] ").strip()
        except EOFError:
            render.console.print()
            break
        except KeyboardInterrupt:
            render.console.print()
            continue
        if not line:
            continue

        if line.startswith("/"):
            command, _, argument = line.partition(" ")
            command = command.lower()
            if command in {"/exit", "/quit"}:
                break
            if command == "/help":
                render.console.print(
                    "[bold]Commands[/bold]\n"
                    "  /tables              list tables\n"
                    "  /describe <table>    show table details\n"
                    "  /schema              show the database overview\n"
                    "  /sql <query>         run read-only SQL\n"
                    "  /clear               clear history\n"
                    "  /exit                exit"
                )
                continue
            if command == "/clear":
                history.clear()
                render.console.clear()
                render.info("History cleared.")
                continue
            if command == "/tables":
                render.print_tables(catalog)
                continue
            if command == "/schema":
                render.print_overview(catalog, index_hint=index_summary(index))
                continue
            if command == "/describe":
                if not argument.strip():
                    render.warn("Usage: /describe <table>")
                    continue
                try:
                    render.print_table_detail(catalog, _find_table(catalog, argument.strip()))
                except CatalogError as exc:
                    render.fail(str(exc))
                continue
            if command == "/sql":
                if not argument.strip():
                    render.warn("Usage: /sql SELECT ...")
                    continue
                try:
                    result = run_readonly_query(
                        db_path,
                        argument,
                        max_rows=settings.max_rows,
                        timeout=settings.query_timeout,
                    )
                    render.print_query_result(result)
                except SqlitexError as exc:
                    render.fail(str(exc))
                continue
            render.warn(f"Unknown command: {command}. Try /help.")
            continue

        with render.console.status("Thinking…") as status:
            result = answer_question(
                line,
                catalog=catalog,
                index=index,
                db_path=db_path,
                client=client,
                settings=settings,
                max_rows=settings.max_rows,
                history=history,
                on_step=lambda message: status.update(f"[cyan]{message}[/cyan]"),
            )
        render.print_answer(result, show_sql=show_sql)
        if not result.error:
            history.append(HistoryTurn(question=line, sql=result.sql, answer=result.answer))
            history = history[-settings.history_turns :] if settings.history_turns else []


@app.command()
@guard
def schema(
    ctx: typer.Context,
    table: Annotated[str | None, typer.Argument(help="Table or view to describe.")] = None,
    db: Annotated[Path | None, typer.Option("--db", "-d", help="Database.")] = None,
    mermaid: Annotated[bool, typer.Option("--mermaid", help="Mermaid entity-relationship diagram.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output.")] = False,
) -> None:
    """Show the catalog, a specific table, or an ER diagram."""

    settings = _state(ctx).settings
    catalog_path, catalog = resolve_catalog(settings, db)

    if mermaid:
        render.print_mermaid(catalog)
        return

    if json_output:
        if table:
            _emit_json(_find_table(catalog, table).model_dump())
        else:
            _emit_json(catalog.model_dump())
        return

    if table:
        render.print_table_detail(catalog, _find_table(catalog, table))
        return

    index = load_index(catalog_path, catalog, settings)
    hint = index_summary(index) if index else "no index (run `sqlitex reindex`)"
    render.print_overview(catalog, index_hint=hint)


@app.command()
@guard
def describe(
    ctx: typer.Context,
    table: Annotated[str, typer.Argument(help="Table or view to describe.")],
    db: Annotated[Path | None, typer.Option("--db", "-d", help="Database.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output.")] = False,
) -> None:
    """Describe a table and its columns in detail."""

    settings = _state(ctx).settings
    _, catalog = resolve_catalog(settings, db)
    item = _find_table(catalog, table)
    if json_output:
        _emit_json(item.model_dump())
    else:
        render.print_table_detail(catalog, item)


@app.command()
@guard
def query(
    ctx: typer.Context,
    sql: Annotated[list[str], typer.Argument(help="Read-only SQL query.")],
    db: Annotated[Path | None, typer.Option("--db", "-d", help="Database.")] = None,
    max_rows: Annotated[int | None, typer.Option("--max-rows", "-n", help="Maximum rows.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output.")] = False,
) -> None:
    """Run SQL manually (always in read-only mode)."""

    settings = _state(ctx).settings
    text = " ".join(sql).strip()
    if not text:
        raise ConfigError("Write a SQL query.")
    db_path = resolve_db_path(settings, db)
    result = run_readonly_query(
        db_path,
        text,
        max_rows=max_rows or settings.max_rows,
        timeout=settings.query_timeout,
    )
    if json_output:
        _emit_json(result.to_dict())
    else:
        render.print_query_result(result)


@app.command()
@guard
def reindex(
    ctx: typer.Context,
    db: Annotated[Path | None, typer.Option("--db", "-d", help="Database.")] = None,
    lexical: Annotated[bool, typer.Option("--lexical", help="Lexical index only, no embeddings.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output.")] = False,
) -> None:
    """Rebuild the RAG index (useful after changing the embedding model)."""

    settings = _state(ctx).settings
    catalog_path, catalog = resolve_catalog(settings, db)
    client = LLMClient(settings)
    index_warnings: list[str] = []
    if not json_output and not lexical and not settings.embeddings_available:
        render.warn("Embeddings are not configured; using a lexical index.")
    if json_output:
        index = build_index(
            catalog,
            catalog_path,
            settings,
            client,
            use_embeddings=not lexical,
            on_message=index_warnings.append,
        )
    else:
        with render.console.status("Rebuilding index…"):
            index = build_index(
                catalog,
                catalog_path,
                settings,
                client,
                use_embeddings=not lexical,
                on_message=render.warn,
            )
    if json_output:
        _emit_json(
            {
                "catalog": str(catalog_path),
                "documents": len(index.docs),
                "vectors": index.vector_ready,
                "model": index.model,
                "built_at": index.built_at,
                "warnings": index_warnings,
            }
        )
        return
    render.ok(f"Index rebuilt · {index_summary(index)}")


@app.command()
@guard
def status(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output.")] = False,
) -> None:
    """List available catalogs."""

    settings = _state(ctx).settings
    files = iter_catalog_files(settings)
    rows: list[dict[str, str]] = []
    payload: list[dict[str, Any]] = []
    for path in files:
        try:
            catalog = load_catalog(path)
        except CatalogError as exc:
            rows.append(
                {
                    "database": path.name,
                    "tables": "—",
                    "relationships": "—",
                    "generated": "—",
                    "index": "—",
                    "status": str(exc),
                }
            )
            payload.append({"catalog": str(path), "error": str(exc)})
            continue
        index = load_index(path, catalog, settings)
        meta_path, _ = index_paths(path)
        if index is not None:
            index_text = "vector" if index.vector_ready else "lexical"
        elif meta_path.exists():
            index_text = "outdated"
        else:
            index_text = "no index"
        db_ok = Path(catalog.database).is_file()
        rows.append(
            {
                "database": catalog.database,
                "tables": str(len(catalog.tables)),
                "relationships": str(len(catalog.relationships)),
                "generated": catalog.generated_at[:10],
                "index": index_text,
                "status": "ok" if db_ok else "missing .sqlite file",
            }
        )
        payload.append(
            {
                "catalog": str(path),
                "database": catalog.database,
                "tables": len(catalog.tables),
                "relationships": len(catalog.relationships),
                "generated_at": catalog.generated_at,
                "index": index_text,
                "database_exists": db_ok,
            }
        )

    if json_output:
        _emit_json({"home": str(settings.home), "catalogs": payload})
        return
    if not rows:
        render.warn(f"No catalogs in {settings.home}. Run `sqlitex init <file.sqlite>`.")
        return
    render.console.print(f"[dim]SQLITEX_HOME: {settings.home}[/dim]")
    render.print_catalog_status(rows)


@app.command()
@guard
def doctor(
    ctx: typer.Context,
    db: Annotated[Path | None, typer.Option("--db", "-d", help="Database to check.")] = None,
    full: Annotated[bool, typer.Option("--full", help="Also test the embeddings endpoint.")] = False,
) -> None:
    """Check configuration, database, catalog, index, and API."""

    settings = _state(ctx).settings
    failures = 0
    render.console.print(f"[bold]SQLiteX {__version__}[/bold] · diagnostics\n")

    render.print_check(
        "Python and SQLite",
        True,
        f"Python {__import__('sys').version.split()[0]} · SQLite {__import__('sqlite3').sqlite_version}",
    )
    render.print_check(
        "Data directory",
        settings.home.is_dir() or None,
        str(settings.home) + ("" if settings.home.is_dir() else " (created on first init)"),
    )

    catalog = None
    try:
        catalog_path, catalog = resolve_catalog(settings, db)
        render.print_check("Catalog", True, str(catalog_path))
    except SqlitexError as exc:
        render.print_check("Catalog", False, str(exc))
        failures += 1
        catalog_path = None

    if catalog is not None:
        db_path = Path(catalog.database)
        render.print_check("Database", db_path.is_file(), str(db_path))
        if not db_path.is_file():
            failures += 1
        assert catalog_path is not None
        index = load_index(catalog_path, catalog, settings)
        if index is not None:
            render.print_check("RAG index", True, index_summary(index))
        else:
            render.print_check("RAG index", None, "missing or outdated; run `sqlitex reindex`")

    render.print_check(
        "AI configured",
        settings.ai_available,
        f"{settings.model} @ {settings.api_base_url}" if settings.ai_available else "missing .env variables",
    )
    if not settings.ai_available:
        failures += 1
    else:
        client = LLMClient(settings)
        try:
            models = client.list_models()
            suffix = f"{len(models)} models available" if models else "connection OK"
            render.print_check("Chat API", True, suffix)
        except SqlitexError as exc:
            render.print_check("Chat API", False, str(exc))
            failures += 1

        if full:
            if settings.embeddings_available:
                try:
                    dimension = client.check_embeddings()
                    render.print_check("Embeddings", True, f"{settings.embed_model} · {dimension} dimensions")
                except SqlitexError as exc:
                    render.print_check("Embeddings", False, str(exc))
                    failures += 1
            else:
                render.print_check("Embeddings", None, "disabled; lexical search will be used")

    render.console.print()
    if failures:
        render.warn(f"{failures} check(s) have problems.")
        raise typer.Exit(1)
    render.ok("Everything looks good.")


@app.command(name="config")
@guard
def config_command(
    ctx: typer.Context,
    json_output: Annotated[bool, typer.Option("--json", help="JSON output.")] = False,
) -> None:
    """Show the effective configuration (without revealing the API key)."""

    settings = _state(ctx).settings
    database = _optional_database(settings)
    env_path = env_file_path()
    if json_output:
        _emit_json(
            {
                "home": str(settings.home),
                "database": str(database) if database else None,
                "env_file": str(env_path) if env_path else None,
                "api_base_url": settings.api_base_url,
                "api_key_set": bool(settings.api_key),
                "model": settings.model,
                "embed_model": settings.embed_model,
                "embed_base_url": settings.embed_base_url,
                "embed_api_key_set": bool(settings.embed_api_key),
                "embeddings": settings.embeddings,
                "language": settings.language,
                "retrieval": settings.retrieval,
                "top_k": settings.top_k,
                "max_rows": settings.max_rows,
                "query_timeout": settings.query_timeout,
                "context_max_chars": settings.context_max_chars,
                "sql_retries": settings.sql_retries,
                "enrich_workers": settings.enrich_workers,
                "debug": settings.debug,
            }
        )
        return
    render.print_config(settings, env_path, database)


@app.command()
@guard
def demo(
    path: Annotated[Path, typer.Argument(help="Where to create the demo database.")] = Path("demo.sqlite"),
    force: Annotated[bool, typer.Option("--force", help="Overwrite the file if it exists.")] = False,
) -> None:
    """Create a sample SQLite database (e-commerce)."""

    created = create_demo_database(path, force=force)
    render.ok(f"Demo database created at [bold]{created}[/bold]")
    render.info(f"Now: [bold]sqlitex init {created}[/bold]")
    render.info(f'Then: [bold]sqlitex ask "what are the best-selling products?" --db {created}[/bold]')


@app.command()
def version() -> None:
    """Show the SQLiteX version."""

    render.console.print(f"sqlitex {__version__}")


if __name__ == "__main__":
    app()
