from __future__ import annotations

import io

from rich.console import Console

from sqlitex import render
from sqlitex.ask import AskResult
from sqlitex.models import Catalog, Relationship
from sqlitex.query import QueryResult


def test_build_mermaid(catalog):
    diagram = render.build_mermaid(catalog)
    assert diagram.startswith("erDiagram")
    assert "orders {" in diagram
    assert "INTEGER id PK" in diagram
    assert "customers ||--o{ orders" in diagram
    assert '"customer_id"' in diagram


def test_build_mermaid_inferred_is_dashed(catalog):
    catalog.relationships = [
        Relationship(
            from_table="orders",
            from_column="customer_id",
            to_table="customers",
            to_column="id",
            kind="inferred",
        )
    ]
    diagram = render.build_mermaid(catalog)
    assert "customers ||..o{ orders" in diagram


def test_build_mermaid_sanitizes_names():
    catalog = Catalog(
        database="/tmp/x.sqlite",
        database_name="x",
        generated_at="2026-01-01",
        tables=[
            {
                "name": "my table",
                "kind": "table",
                "columns": [{"name": "total price", "declared_type": "VARCHAR(10)", "primary_key": True}],
            }
        ],
    )
    diagram = render.build_mermaid(catalog)
    assert "my_table {" in diagram
    assert "VARCHAR_10 total_price PK" in diagram


def test_print_overview_smoke(catalog, monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(render, "console", Console(file=stream, width=120))
    render.print_overview(catalog, index_hint="9 documents")
    output = stream.getvalue()
    assert "demo" in output
    assert "9 documents" in output


def test_print_table_detail_smoke(catalog, monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(render, "console", Console(file=stream, width=140))
    table = next(item for item in catalog.tables if item.name == "orders")
    render.print_table_detail(catalog, table)
    output = stream.getvalue()
    assert "orders" in output
    assert "customer_id" in output
    assert "Sample data" in output


def test_print_answer_smoke(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(render, "console", Console(file=stream, width=120))
    result = AskResult(
        question="How many?",
        answer="There are **45** customers.",
        sql="SELECT COUNT(*) FROM customers",
        columns=["n"],
        rows=[[45]],
        retrieved_tables=["customers"],
        elapsed_ms=123,
        followups=["And orders?"],
    )
    render.print_answer(result)
    output = stream.getvalue()
    assert "Answer" in output
    assert "Executed SQL" in output
    assert "45" in output
    assert "And orders?" in output


def test_print_query_result_smoke(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(render, "console", Console(file=stream, width=100))
    render.print_query_result(
        QueryResult(sql="SELECT 1", columns=["n", "name"], rows=[[1, "Ana"], [2, None]])
    )
    output = stream.getvalue()
    assert "Ana" in output
    assert "NULL" in output


def test_print_query_result_empty(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(render, "console", Console(file=stream, width=100))
    render.print_query_result(QueryResult(sql="SELECT 1", columns=["n"], rows=[]))
    assert "No rows" in stream.getvalue()
