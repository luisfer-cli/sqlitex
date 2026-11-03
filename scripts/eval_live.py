"""End-to-end evaluation of SQLiteX with synthetic databases and SQL ground truth.

Usage:
    uv run python scripts/eval_live.py
    uv run python scripts/eval_live.py --only hr,iot --no-reuse
    uv run python scripts/eval_live.py --embeddings

Requires a `.env` with a configured AI provider. Results are written to
`eval-results/report.json` and `eval-results/report.md`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_data import (  # noqa: E402
    build_edge,
    build_edu,
    build_hr,
    build_iot,
    build_mini,
)

from sqlitex.ask import AskResult, answer_question  # noqa: E402
from sqlitex.catalog import (  # noqa: E402
    catalog_path_for,
    index_paths,
    load_catalog,
    save_catalog,
)
from sqlitex.config import settings_from_env  # noqa: E402
from sqlitex.demo import create_demo_database  # noqa: E402
from sqlitex.enrich import EnrichReport, enrich_catalog  # noqa: E402
from sqlitex.errors import SqlitexError  # noqa: E402
from sqlitex.index import build_index, load_index  # noqa: E402
from sqlitex.introspect import introspect_database  # noqa: E402
from sqlitex.llm import LLMClient  # noqa: E402

Kind = Literal["scalar", "top", "set", "ratio"]


@dataclass(frozen=True)
class Case:
    question: str
    expected_sql: str
    kind: Kind = "scalar"
    tolerance: float = 0.01


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    builder: Callable[[Path], None]
    cases: tuple[Case, ...]


def _demo(path: Path) -> None:
    create_demo_database(path, force=True)


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        key="ecommerce",
        title="E-commerce (demo)",
        builder=_demo,
        cases=(
            Case(
                "Which category generated the most revenue?",
                "SELECT category, ROUND(SUM(revenue), 2) FROM product_sales_summary "
                "GROUP BY category ORDER BY 2 DESC LIMIT 1",
                "top",
                1.0,
            ),
            Case(
                "How many delivered orders were there in 2025?",
                "SELECT COUNT(*) FROM orders WHERE status = 'delivered' "
                "AND ordered_at >= '2025-01-01' AND ordered_at < '2026-01-01'",
                "scalar",
                0,
            ),
            Case(
                "Which products share the best average review rating (with at least 4 reviews)?",
                "WITH stats AS ("
                "SELECT p.name, ROUND(AVG(r.rating), 2) AS average, COUNT(*) AS n "
                "FROM reviews r JOIN products p ON p.id = r.product_id "
                "GROUP BY p.id, p.name HAVING COUNT(*) >= 4"
                "), best AS (SELECT MAX(average) AS m FROM stats) "
                "SELECT name FROM stats, best WHERE average = m ORDER BY name",
                "set",
            ),
        ),
    ),
    Dataset(
        key="hr",
        title="Human Resources",
        builder=build_hr,
        cases=(
            Case(
                "Which department has the highest average salary among active employees?",
                "SELECT d.name, ROUND(AVG(e.salary), 2) "
                "FROM employees e JOIN departments d ON d.id = e.department_id "
                "WHERE e.active = 1 GROUP BY d.id ORDER BY 2 DESC LIMIT 1",
                "top",
                0.02,
            ),
            Case(
                "How many hours were spent in total on the 'ERP Migration' project?",
                "SELECT ROUND(SUM(a.hours), 2) FROM assignments a "
                "JOIN projects p ON p.id = a.project_id WHERE p.name = 'ERP Migration'",
                "scalar",
                0.01,
            ),
            Case(
                "How many active employees have no manager?",
                "SELECT COUNT(*) FROM employees WHERE active = 1 AND manager_id IS NULL",
                "scalar",
                0,
            ),
            Case(
                "What was the total net amount paid in the 2026-09 payroll?",
                "SELECT ROUND(SUM(net), 2) FROM payroll WHERE period = '2026-09'",
                "scalar",
                1.0,
            ),
        ),
    ),
    Dataset(
        key="iot",
        title="IoT sensors",
        builder=build_iot,
        cases=(
            Case(
                "Which device (code) recorded the highest average temperature "
                "between October 1 and October 31, 2026?",
                "SELECT d.code, ROUND(AVG(r.temperature), 2) "
                "FROM readings r JOIN devices d ON d.id = r.device_id "
                "WHERE r.taken_at >= '2026-10-01' AND r.taken_at < '2026-11-01' "
                "GROUP BY d.id ORDER BY 2 DESC LIMIT 1",
                "top",
                0.05,
            ),
            Case(
                "How many critical alerts are still unresolved?",
                "SELECT COUNT(*) FROM alerts WHERE severity = 'critical' AND resolved = 0",
                "scalar",
                0,
            ),
            Case(
                "Which site has the most installed devices?",
                "SELECT s.name, COUNT(*) FROM devices d "
                "JOIN sites s ON s.id = d.site_id GROUP BY s.id ORDER BY 2 DESC LIMIT 1",
                "top",
                0,
            ),
            Case(
                "What is the total cost of maintenance completed in 2026?",
                "SELECT ROUND(SUM(cost), 2) FROM maintenance "
                "WHERE completed_at >= '2026-01-01' AND completed_at < '2027-01-01'",
                "scalar",
                1.0,
            ),
        ),
    ),
    Dataset(
        key="education",
        title="Online academy",
        builder=build_edu,
        cases=(
            Case(
                "What is the average exam grade for the 'Data Science 101' course?",
                "SELECT ROUND(AVG(e.grade), 2) FROM exams e "
                "JOIN enrollments en ON en.id = e.enrollment_id "
                "JOIN courses c ON c.id = en.course_id WHERE c.name = 'Data Science 101'",
                "scalar",
                0.05,
            ),
            Case(
                "Which teacher teaches the most courses?",
                "SELECT t.name, COUNT(*) FROM courses c "
                "JOIN teachers t ON t.id = c.teacher_id "
                "GROUP BY t.id ORDER BY 2 DESC LIMIT 1",
                "top",
                0,
            ),
            Case(
                "How many students have an average grade below 5 (with at least one exam)?",
                "SELECT COUNT(*) FROM (SELECT en.student_id FROM exams e "
                "JOIN enrollments en ON en.id = e.enrollment_id "
                "GROUP BY en.student_id HAVING AVG(e.grade) < 5)",
                "scalar",
                0,
            ),
            Case(
                "What is the attendance rate for the 'Advanced SQL' course?",
                "SELECT ROUND(AVG(a.present), 4) FROM attendance a "
                "JOIN enrollments en ON en.id = a.enrollment_id "
                "JOIN courses c ON c.id = en.course_id WHERE c.name = 'Advanced SQL'",
                "ratio",
                0.01,
            ),
            Case(
                "Which student has the highest average grade (with at least 2 exams)?",
                "SELECT s.name, ROUND(AVG(e.grade), 2) FROM exams e "
                "JOIN enrollments en ON en.id = e.enrollment_id "
                "JOIN students s ON s.id = en.student_id "
                "GROUP BY s.id HAVING COUNT(*) >= 2 ORDER BY 2 DESC LIMIT 1",
                "top",
                0.05,
            ),
        ),
    ),
    Dataset(
        key="edge_schema",
        title="Irregular schema without FKs",
        builder=build_edge,
        cases=(
            Case(
                "Which customer spent the most on non-cancelled orders?",
                'SELECT c."Full Name", ROUND(SUM(o.total), 2) FROM "Customers" c '
                'JOIN "orders_2026" o ON o.customer_id = c.id '
                "WHERE o.status <> 'cancelled' "
                "GROUP BY c.id ORDER BY 2 DESC LIMIT 1",
                "top",
                1.0,
            ),
            Case(
                "Which product sold the most units in non-cancelled orders?",
                "SELECT c.name, SUM(l.quantity) FROM catalog c "
                "JOIN order_lines l ON l.product_id = c.id "
                'JOIN "orders_2026" o ON o.id = l.order_id '
                "WHERE o.status <> 'cancelled' "
                "GROUP BY c.id ORDER BY 2 DESC LIMIT 1",
                "top",
                0,
            ),
            Case(
                "How many orders have the status 'paid'?",
                "SELECT COUNT(*) FROM \"orders_2026\" WHERE status = 'paid'",
                "scalar",
                0,
            ),
            Case(
                "Which 'Audio' products cost less than 100?",
                "SELECT name FROM catalog WHERE category = 'Audio' AND price < 100 ORDER BY name",
                "set",
            ),
            Case(
                "Which wireless products are there (by name or description)?",
                "SELECT name FROM search_index WHERE search_index MATCH 'wireless*' ORDER BY name",
                "set",
            ),
        ),
    ),
    Dataset(
        key="mini",
        title="Mini library",
        builder=build_mini,
        cases=(
            Case(
                "Which book sold the most units?",
                "SELECT b.title, SUM(s.quantity) FROM sales s "
                "JOIN books b ON b.id = s.book_id "
                "GROUP BY b.id ORDER BY 2 DESC LIMIT 1",
                "top",
                0,
            ),
            Case(
                "How many books are in the 'Science Fiction' genre?",
                "SELECT COUNT(*) FROM books WHERE genre = 'Science Fiction'",
                "scalar",
                0,
            ),
            Case(
                "What is the total revenue from all sales?",
                "SELECT ROUND(SUM(quantity * unit_price), 2) FROM sales",
                "scalar",
                1.0,
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------


def _norm(text: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text))
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def _flatten(rows: list[tuple[Any, ...]] | list[list[Any]]) -> tuple[list[float], list[str]]:
    numbers: list[float] = []
    texts: list[str] = []
    for row in rows:
        for value in row:
            if isinstance(value, (bool, int, float)):
                numbers.append(float(value))
            elif isinstance(value, str) and value.strip():
                texts.append(value)
    return numbers, texts


def _num_ok(expected: float, actual: float, case: Case) -> bool:
    candidates = [expected]
    if case.kind == "ratio":
        candidates.append(expected * 100)
    for index, candidate in enumerate(candidates):
        tolerance = case.tolerance if index == 0 else case.tolerance * 100
        if abs(actual - candidate) <= tolerance:
            return True
    return False


def _truth_ok(case: Case, expected_rows, actual_rows) -> bool:
    expected_numbers, expected_texts = _flatten(expected_rows)
    actual_numbers, actual_texts = _flatten(actual_rows)
    if case.kind == "set":
        if not expected_texts:
            return False
        generated = " | ".join(_norm(text) for text in actual_texts)
        return all(_norm(text) in generated for text in expected_texts)
    if not expected_numbers:
        return False
    if case.kind == "top":
        if not expected_texts:
            return False
        name_ok = any(_norm(expected_texts[0]) in _norm(text) for text in actual_texts)
        number_ok = any(_num_ok(expected_numbers[0], value, case) for value in actual_numbers)
        return name_ok and number_ok
    return any(_num_ok(expected_numbers[0], value, case) for value in actual_numbers)


_NUMBER_TOKEN = re.compile(r"\d[\d.,]*")


def _number_in_text(value: float, text: str, *, ratio: bool = False) -> bool:
    tokens = {re.sub(r"\D", "", token) for token in _NUMBER_TOKEN.findall(text)}
    values = [abs(float(value))]
    if ratio:
        values.append(abs(float(value)) * 100)
    for number in values:
        formats = {
            f"{number:.0f}",
            f"{number:.1f}",
            f"{number:.2f}",
            f"{number:g}",
        }
        for formatted in formats:
            digits = re.sub(r"\D", "", formatted)
            if digits and digits in tokens:
                return True
    return False


def _answer_ok(case: Case, expected_rows, answer: str) -> bool:
    expected_numbers, expected_texts = _flatten(expected_rows)
    if not answer:
        return False
    if case.kind == "set":
        hits = sum(1 for text in expected_texts if _norm(text) in _norm(answer))
        return hits >= max(1, math.ceil(len(expected_texts) * 0.6))
    if not expected_numbers:
        return False
    text_ok = True
    if case.kind == "top" and expected_texts:
        text_ok = _norm(expected_texts[0]) in _norm(answer)
    numbers_ok = all(
        _number_in_text(number, answer, ratio=case.kind == "ratio") for number in expected_numbers
    )
    return text_ok and numbers_ok


def _read_rows(db_path: Path, sql: str) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<blob {len(bytes(value))} bytes>"
    return value


def _rows_json(rows) -> list[list[Any]]:
    return [[_jsonable(value) for value in row] for row in rows]


def _summary(rows, limit: int = 3) -> str:
    if not rows:
        return "(no rows)"
    parts = []
    for row in rows[:limit]:
        parts.append(" | ".join(str(_jsonable(value)) for value in row))
    if len(rows) > limit:
        parts.append(f"… (+{len(rows) - limit})")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class DatasetResult:
    key: str
    title: str
    init: dict[str, Any] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)


def _ensure_catalog(
    db_path: Path,
    settings,
    client: LLMClient,
    *,
    reuse: bool,
    use_embeddings: bool,
) -> tuple[Any, Any, EnrichReport | None, float]:
    catalog_path = catalog_path_for(db_path, settings)
    fresh = introspect_database(db_path, settings)
    if reuse and catalog_path.exists():
        try:
            stored = load_catalog(catalog_path)
        except SqlitexError:
            stored = None
        if stored is not None and stored.fingerprint == fresh.fingerprint:
            index = load_index(catalog_path, stored, settings)
            if index is None:
                index = build_index(stored, catalog_path, settings, client, use_embeddings=use_embeddings)
            return stored, index, None, 0.0

    started = time.perf_counter()
    report = enrich_catalog(fresh, settings, client, force=True)
    catalog_path = save_catalog(fresh, settings)
    index = build_index(fresh, catalog_path, settings, client, use_embeddings=use_embeddings)
    elapsed = time.perf_counter() - started
    return fresh, index, report, elapsed


def _run_dataset(
    dataset: Dataset,
    *,
    settings,
    client: LLMClient,
    data_dir: Path,
    reuse: bool,
    use_embeddings: bool,
    verbose: bool,
) -> DatasetResult:
    result = DatasetResult(key=dataset.key, title=dataset.title)
    db_path = data_dir / f"{dataset.key}.sqlite"
    if not db_path.exists():
        print(f"  creating synthetic database {db_path.name}…")
        dataset.builder(db_path)

    print("  analyzing with AI…")
    started = time.perf_counter()
    catalog, index, report, init_seconds = _ensure_catalog(
        db_path,
        settings,
        client,
        reuse=reuse,
        use_embeddings=use_embeddings,
    )
    if report is None:
        init_seconds = time.perf_counter() - started
    annotated = sum(1 for table in catalog.tables if table.ai is not None)
    result.init = {
        "objects": len(catalog.tables),
        "tables": catalog.stats.tables,
        "views": catalog.stats.views,
        "annotated": annotated,
        "global_summary": catalog.global_ai is not None,
        "relationships": len(catalog.relationships),
        "inferred_relationships": sum(1 for rel in catalog.relationships if rel.kind == "inferred"),
        "warnings": list(catalog.warnings),
        "seconds": round(init_seconds, 2),
        "reused": report is None,
        "failed_annotations": report.failed if report else [],
        "index_vectors": index.vector_ready,
        "index_model": index.model,
    }
    print(
        f"  init: {annotated}/{len(catalog.tables)} objects annotated · "
        f"{len(catalog.relationships)} relationships · {init_seconds:.1f}s"
    )

    for number, case in enumerate(dataset.cases, start=1):
        expected_rows = _read_rows(db_path, case.expected_sql)
        ask_started = time.perf_counter()
        try:
            ask_result = answer_question(
                case.question,
                catalog=catalog,
                index=index,
                db_path=db_path,
                client=client,
                settings=settings,
            )
            ask_error = ask_result.error
        except SqlitexError as exc:
            ask_result = AskResult(question=case.question, error=str(exc))
            ask_error = str(exc)
            ask_result.elapsed_ms = (time.perf_counter() - ask_started) * 1000
        except Exception as exc:
            ask_result = AskResult(question=case.question, error=f"{type(exc).__name__}: {exc}")
            ask_error = ask_result.error
            ask_result.elapsed_ms = (time.perf_counter() - ask_started) * 1000

        actual_rows = [list(row) for row in ask_result.rows]
        sql_ok = ask_error is None
        data_ok = sql_ok and _truth_ok(case, expected_rows, actual_rows)
        answer_ok = sql_ok and _answer_ok(case, expected_rows, ask_result.answer)
        entry = {
            "question": case.question,
            "kind": case.kind,
            "expected_sql": case.expected_sql,
            "expected_rows": _rows_json(expected_rows),
            "sql": ask_result.sql,
            "columns": ask_result.columns,
            "rows": ask_result.rows,
            "answer": ask_result.answer,
            "error": ask_error,
            "retrieved_tables": ask_result.retrieved_tables,
            "attempts": ask_result.attempts,
            "elapsed_ms": round(ask_result.elapsed_ms, 1),
            "sql_ok": sql_ok,
            "data_ok": data_ok,
            "answer_ok": answer_ok,
        }
        result.cases.append(entry)
        mark = "✅" if data_ok and answer_ok else ("⚠️" if data_ok else "❌")
        print(f"  {mark} {number:02d}. {case.question[:72]} ({ask_result.elapsed_ms / 1000:.1f}s)")
        if verbose and not (data_ok and answer_ok):
            print(f"      SQL: {ask_result.sql}")
            print(f"      Expected: {_summary(expected_rows)}")
            print(f"      Got: {_summary(actual_rows)}")
            print(f"      Answer: {ask_result.answer[:300]}")
            if ask_error:
                print(f"      Error: {ask_error}")
    return result


def _totals(results: list[DatasetResult]) -> dict[str, Any]:
    cases = [case for dataset in results for case in dataset.cases]
    total = len(cases)
    return {
        "questions": total,
        "sql_ok": sum(1 for case in cases if case["sql_ok"]),
        "data_ok": sum(1 for case in cases if case["data_ok"]),
        "answer_ok": sum(1 for case in cases if case["answer_ok"]),
        "both_ok": sum(1 for case in cases if case["data_ok"] and case["answer_ok"]),
        "avg_latency_ms": round(sum(case["elapsed_ms"] for case in cases) / total, 1) if total else 0,
        "annotated": sum(dataset.init.get("annotated", 0) for dataset in results),
        "objects": sum(dataset.init.get("objects", 0) for dataset in results),
    }


def _markdown(results: list[DatasetResult], meta: dict[str, Any], totals: dict[str, Any]) -> str:
    lines = [
        "# SQLiteX end-to-end evaluation",
        "",
        f"- **Date:** {meta['generated_at']}",
        f"- **Model:** `{meta['model']}`",
        f"- **Endpoint:** `{meta['base_url']}`",
        f"- **Retrieval:** `{meta['retrieval']}`",
        f"- **Embeddings:** {'yes' if meta['embeddings'] else 'no (BM25 lexical)'}",
        f"- **Questions:** {totals['questions']}",
        "",
        "## Summary",
        "",
        "| Dataset | Objects | Annotated | Rel. | Init | Qs | SQL OK | Data OK | Answer OK | Latency |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for dataset in results:
        init = dataset.init
        cases = dataset.cases
        data_ok = sum(1 for case in cases if case["data_ok"])
        answer_ok = sum(1 for case in cases if case["answer_ok"])
        avg_latency = sum(case["elapsed_ms"] for case in cases) / len(cases) if cases else 0
        lines.append(
            f"| {dataset.title} | {init['objects']} | {init['annotated']} | "
            f"{init['relationships']} | {init['seconds']:.1f}s | {len(cases)} | "
            f"{sum(1 for case in cases if case['sql_ok'])}/{len(cases)} | "
            f"{data_ok}/{len(cases)} | {answer_ok}/{len(cases)} | {avg_latency:.0f} ms |"
        )
    lines.extend(
        [
            f"| **Total** | {totals['objects']} | {totals['annotated']} | — | — | "
            f"{totals['questions']} | {totals['sql_ok']}/{totals['questions']} | "
            f"{totals['data_ok']}/{totals['questions']} | "
            f"{totals['answer_ok']}/{totals['questions']} | {totals['avg_latency_ms']:.0f} ms |",
            "",
            "> **Data OK**: the SQL result matches the computed ground truth. "
            "**Answer OK**: the text answer contains the key figure/name.",
            "",
            "## Details by dataset",
            "",
        ]
    )
    for dataset in results:
        init = dataset.init
        lines.extend(
            [
                f"### {dataset.title}",
                "",
                f"- Objects: {init['objects']} · annotated: {init['annotated']} · "
                f"relationships: {init['relationships']} "
                f"({init['inferred_relationships']} inferred)",
                f"- Global summary: {'yes' if init['global_summary'] else 'no'} · "
                f"index: {'vector' if init['index_vectors'] else 'lexical'} · "
                f"init: {init['seconds']:.1f}s" + (" · reused" if init["reused"] else ""),
            ]
        )
        if init["warnings"]:
            lines.append("- Warnings: " + " | ".join(init["warnings"][:4]))
        lines.extend(
            [
                "",
                "| # | Question | SQL | Data | Answer | Expected | Got |",
                "| ---: | --- | :---: | :---: | :---: | --- | --- |",
            ]
        )
        for index, case in enumerate(dataset.cases, start=1):
            expected = _summary([tuple(row) for row in case["expected_rows"]]).replace("|", "\\|")
            obtained = _summary(case["rows"]).replace("|", "\\|")
            question_text = case["question"].replace("|", "\\|")
            lines.append(
                f"| {index} | {question_text} | "
                f"{'✅' if case['sql_ok'] else '❌'} | "
                f"{'✅' if case['data_ok'] else '❌'} | "
                f"{'✅' if case['answer_ok'] else '❌'} | "
                f"{expected[:180]} | {obtained[:180]} |"
            )
        lines.append("")

        failures = [case for case in dataset.cases if not (case["data_ok"] and case["answer_ok"])]
        if failures:
            lines.append("#### Cases to review")
            lines.append("")
            for case in failures:
                status = []
                if not case["sql_ok"]:
                    status.append("SQL failed")
                if not case["data_ok"]:
                    status.append("data mismatch")
                if not case["answer_ok"]:
                    status.append("answer missing the key fact")
                lines.append(f"**{case['question']}** ({'; '.join(status)})")
                lines.append("")
                lines.append(f"- Generated SQL: `{case['sql']}`")
                lines.append(f"- Expected: {_summary([tuple(row) for row in case['expected_rows']])}")
                lines.append(f"- Got: {_summary(case['rows'])}")
                lines.append(f"- Answer: {case['answer'] or '(empty)'}")
                if case["error"]:
                    lines.append(f"- Error: {case['error']}")
                lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLiteX end-to-end evaluation")
    parser.add_argument(
        "--only",
        help="Comma-separated datasets (ecommerce,hr,iot,education,edge_schema,mini)",
    )
    parser.add_argument("--out", default="eval-results", help="Results directory")
    parser.add_argument("--no-reuse", action="store_true", help="Force re-annotation of all tables")
    parser.add_argument("--rebuild", action="store_true", help="Delete and recreate synthetic databases")
    parser.add_argument(
        "--embeddings", action="store_true", help="Use embeddings if the endpoint supports them"
    )
    parser.add_argument("--verbose", action="store_true", help="Print SQL and rows for each case")
    args = parser.parse_args()

    original_cwd = Path.cwd()
    os.chdir(ROOT)
    out_dir = (original_cwd / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    settings = settings_from_env(home=out_dir / "home")
    if args.embeddings:
        from dataclasses import replace

        settings = replace(settings, embeddings="on")
    client = LLMClient(settings)
    if not client.available:
        print(
            "No AI configured. Create a .env with SQLITEX_API_BASE_URL, SQLITEX_API_KEY, and SQLITEX_MODEL.",
            file=sys.stderr,
        )
        return 2

    selected = {item.strip() for item in args.only.split(",")} if args.only else None
    datasets = [dataset for dataset in DATASETS if not selected or dataset.key in selected]
    if not datasets:
        print("No datasets selected.", file=sys.stderr)
        return 2

    if args.rebuild:
        for dataset in datasets:
            db_path = data_dir / f"{dataset.key}.sqlite"
            if db_path.exists():
                db_path.unlink()
            catalog_path = catalog_path_for(db_path, settings)
            for path in (catalog_path, *index_paths(catalog_path)):
                if path.exists():
                    path.unlink()

    print(f"Model: {settings.model} @ {settings.api_base_url}")
    print(f"Results: {out_dir}")
    results: list[DatasetResult] = []
    for dataset in datasets:
        print(f"\n=== {dataset.title} ===")
        result = _run_dataset(
            dataset,
            settings=settings,
            client=client,
            data_dir=data_dir,
            reuse=not args.no_reuse,
            use_embeddings=args.embeddings,
            verbose=args.verbose,
        )
        results.append(result)

    totals = _totals(results)
    meta = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": settings.model,
        "base_url": settings.api_base_url,
        "retrieval": settings.retrieval,
        "embeddings": args.embeddings,
    }
    payload = {
        "meta": meta,
        "totals": totals,
        "datasets": [
            {"key": result.key, "title": result.title, "init": result.init, "cases": result.cases}
            for result in results
        ],
    }
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_markdown(results, meta, totals), encoding="utf-8")

    print("\n=== Summary ===")
    print(
        f"Questions: {totals['questions']} · SQL OK: {totals['sql_ok']} · "
        f"Data OK: {totals['data_ok']} · Answer OK: {totals['answer_ok']} · "
        f"Both OK: {totals['both_ok']}"
    )
    print(f"AI annotations: {totals['annotated']}/{totals['objects']} objects")
    print(f"Average latency: {totals['avg_latency_ms'] / 1000:.1f}s")
    print(f"Report: {out_dir / 'report.md'}")
    return 0 if totals["both_ok"] == totals["questions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
