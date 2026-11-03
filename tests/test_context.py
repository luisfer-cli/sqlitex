from __future__ import annotations

from dataclasses import replace

from sqlitex.context import build_context, result_as_text
from sqlitex.models import AIColumnInfo, AITableInfo


def _table(catalog, name):
    return next(table for table in catalog.tables if table.name == name)


def _add_semantics(catalog):
    orders = _table(catalog, "orders")
    orders.ai = AITableInfo(
        summary="Orders placed by customers.",
        purpose="Record sales.",
        entity="One order.",
        columns={
            "status": AIColumnInfo(meaning="Order lifecycle status."),
            "total": AIColumnInfo(meaning="Total amount in euros."),
        },
        data_quality_notes=["Amounts use two decimals."],
    )
    customers = _table(catalog, "customers")
    customers.ai = AITableInfo(
        summary="People and companies that buy.",
        columns={"email": AIColumnInfo(meaning="Contact email.", pii=True)},
    )


def test_context_includes_semantics_and_relations(catalog, settings):
    _add_semantics(catalog)
    bundle = build_context(catalog, ["orders"], settings)

    assert "orders" in bundle.tables
    assert "customers" in bundle.tables  # FK expansion
    assert "Order lifecycle status." in bundle.text
    assert "orders.customer_id → customers.id" in bundle.text
    assert "Contact email" in bundle.text
    assert "sensitive data" in bundle.text
    assert "CREATE TABLE orders" in bundle.text


def test_context_without_expansion(catalog, settings):
    bundle = build_context(catalog, ["orders"], settings, expand=False)
    assert bundle.tables == ["orders"]
    assert "### Table `customers`" not in bundle.text


def test_context_truncates(catalog, settings):
    limited = replace(settings, context_max_chars=2200)
    names = [table.name for table in catalog.tables]
    bundle = build_context(catalog, names, limited)
    assert bundle.truncated is True
    assert len(bundle.text) <= 2400
    assert "Context truncated" in bundle.text


def test_context_prioritizes_retrieved_tables(catalog, settings):
    bundle = build_context(catalog, ["customers", "orders"], settings, expand=False)
    assert bundle.tables[:2] == ["customers", "orders"]
    assert bundle.text.index("### Table `customers`") < bundle.text.index("### Table `orders`")


def test_context_with_no_tables(catalog, settings):
    bundle = build_context(catalog, [], settings)
    assert bundle.text == "(no tables retrieved)"


def test_result_as_text_formats_values():
    text = result_as_text(
        ["id", "name", "note", "blob"],
        [
            [1, "Ana", None, b"abc"],
            [2, "x" * 300, 4.5, None],
            [3, "Carlos", 2.0, None],
        ],
        max_rows=2,
    )
    assert "id | name | note | blob" in text
    assert "NULL" in text
    assert "<blob 3 bytes>" in text
    assert "1 more rows not included" in text
    assert "x" * 300 not in text


def test_result_as_text_empty():
    text = result_as_text(["a"], [])
    assert "(no rows)" in text
