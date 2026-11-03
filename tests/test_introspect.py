from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sqlitex.introspect import connect_readonly, introspect_database


def _table(catalog, name):
    return next(table for table in catalog.tables if table.name == name)


def test_demo_catalog_structure(catalog):
    assert catalog.database_name == "demo"
    assert catalog.stats.tables == 8
    assert catalog.stats.views == 1
    assert catalog.stats.triggers == 1
    assert catalog.stats.rows and catalog.stats.rows > 700
    assert len(catalog.relationships) == 8


def test_table_columns_and_samples(catalog):
    orders = _table(catalog, "orders")
    assert orders.row_count == 160
    assert [column.name for column in orders.columns] == [
        "id",
        "customer_id",
        "ordered_at",
        "status",
        "channel",
        "total",
    ]
    assert orders.columns[0].primary_key is True
    assert orders.columns[1].foreign_key == "customers.id"
    assert len(orders.sample_rows) == 3
    assert set(orders.sample_rows[0]) == {column.name for column in orders.columns}


def test_profiles_capture_categorical_values(catalog):
    status = next(column for column in _table(catalog, "orders").columns if column.name == "status")
    assert status.profile is not None
    assert status.profile.distinct == 5
    assert status.profile.top_values
    values = {item["value"] for item in status.profile.top_values}
    assert values <= {"pending", "paid", "shipped", "delivered", "cancelled"}


def test_indexes_and_view(catalog):
    orders = _table(catalog, "orders")
    index_names = {index.name for index in orders.indexes}
    assert "idx_orders_customer" in index_names
    view = _table(catalog, "product_sales_summary")
    assert view.kind == "view"
    assert view.row_count == 24
    assert "units_sold" in {column.name for column in view.columns}


def test_relationships_include_declared_and_self_reference(catalog):
    triples = {
        (relationship.from_table, relationship.from_column, relationship.to_table, relationship.to_column)
        for relationship in catalog.relationships
    }
    assert ("orders", "customer_id", "customers", "id") in triples
    assert ("employees", "manager_id", "employees", "id") in triples


def test_inferred_relationship_from_column_name(tmp_path, settings):
    path = tmp_path / "inferred.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            total REAL
        );
        INSERT INTO customers VALUES (1, 'Ana');
        INSERT INTO orders VALUES (1, 1, 10.0);
        """
    )
    conn.close()

    catalog = introspect_database(path, settings)
    inferred = [relationship for relationship in catalog.relationships if relationship.kind == "inferred"]
    assert any(
        (relationship.from_table, relationship.from_column, relationship.to_table)
        == ("orders", "customer_id", "customers")
        for relationship in inferred
    )


def test_fts_shadow_tables_are_hidden(tmp_path, settings):
    path = tmp_path / "fts.sqlite"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            "CREATE VIRTUAL TABLE search_index USING fts5(title, body);"
            "INSERT INTO search_index (title, body) VALUES ('Hello', 'world');"
        )
    except sqlite3.OperationalError:
        conn.close()
        pytest.skip("FTS5 is not available in this SQLite build")
    conn.close()

    catalog = introspect_database(path, settings)
    names = {table.name for table in catalog.tables}
    assert "search_index" in names
    assert not any(name.startswith("search_index_") for name in names)
    assert _table(catalog, "search_index").kind == "virtual"


def test_readonly_connection_rejects_writes(demo_db):
    conn = connect_readonly(demo_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO categories (name) VALUES ('new')")
    finally:
        conn.close()


def test_missing_database_raises(tmp_path, settings):
    from sqlitex.errors import DatabaseError

    with pytest.raises(DatabaseError):
        introspect_database(Path(tmp_path / "nope.sqlite"), settings)


def test_profiles_can_be_disabled(demo_db, settings):
    catalog = introspect_database(demo_db, settings, include_profiles=False)
    assert all(column.profile is None for table in catalog.tables for column in table.columns)
