from __future__ import annotations

import sqlite3

import pytest

from sqlitex.errors import QueryRejected
from sqlitex.query import run_readonly_query, validate_readonly_sql


def test_select_with_limit_and_truncation(demo_db):
    result = run_readonly_query(demo_db, "SELECT * FROM orders ORDER BY id", max_rows=3)
    assert result.columns[:2] == ["id", "customer_id"]
    assert len(result.rows) == 3
    assert result.truncated is True
    assert result.elapsed_ms >= 0


def test_with_query_is_allowed(demo_db):
    result = run_readonly_query(
        demo_db,
        "WITH totals AS (SELECT customer_id, SUM(total) AS t FROM orders GROUP BY customer_id) "
        "SELECT * FROM totals ORDER BY t DESC LIMIT 2",
        max_rows=10,
    )
    assert result.truncated is False
    assert len(result.rows) == 2


def test_empty_result(demo_db):
    result = run_readonly_query(demo_db, "SELECT * FROM orders WHERE total < -999", max_rows=5)
    assert result.rows == []
    assert result.columns


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET total = 0",
        "INSERT INTO categories (name) VALUES ('x')",
        "DROP TABLE orders",
        "PRAGMA table_info(orders)",
        "ATTACH DATABASE 'x.db' AS x",
        "",
        "   ",
    ],
)
def test_writes_and_non_select_are_rejected(sql):
    with pytest.raises(QueryRejected):
        validate_readonly_sql(sql)


def test_multiple_statements_are_rejected(demo_db):
    with pytest.raises(QueryRejected):
        run_readonly_query(demo_db, "SELECT 1; SELECT 2", max_rows=5)


def test_trailing_semicolon_is_removed():
    assert validate_readonly_sql("SELECT 1;  ") == "SELECT 1"


def test_query_timeout(demo_db):
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    with pytest.raises(QueryRejected, match="time limit"):
        run_readonly_query(demo_db, sql, max_rows=10, timeout=0.05)


def test_cte_with_write_is_rejected(demo_db):
    with pytest.raises(QueryRejected):
        run_readonly_query(
            demo_db,
            "WITH x AS (SELECT 1) DELETE FROM orders",
            max_rows=10,
        )


def test_max_rows_must_be_positive(demo_db):
    with pytest.raises(QueryRejected):
        run_readonly_query(demo_db, "SELECT 1", max_rows=0)


def test_fts_queries_are_allowed(tmp_path):
    db = tmp_path / "fts.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE VIRTUAL TABLE search_index USING fts5(name, description);"
            "INSERT INTO search_index VALUES ('Wireless Mouse', 'Mouse without cables');"
            "INSERT INTO search_index VALUES ('Wireless Headphones Pro', 'Noise cancelling');"
            "INSERT INTO search_index VALUES ('Mechanical Keyboard', 'Wired keyboard');"
        )
    except sqlite3.OperationalError:
        conn.close()
        pytest.skip("FTS5 is not available")
    conn.close()

    result = run_readonly_query(
        db,
        "SELECT name FROM search_index WHERE search_index MATCH 'wireless*' ORDER BY name",
        max_rows=10,
    )
    assert [row[0] for row in result.rows] == [
        "Wireless Headphones Pro",
        "Wireless Mouse",
    ]

    like_result = run_readonly_query(
        db,
        "SELECT name FROM search_index WHERE lower(name) LIKE '%wireless%' ORDER BY name",
        max_rows=10,
    )
    assert len(like_result.rows) == 2
