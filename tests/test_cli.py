from __future__ import annotations

import contextlib
import json

from typer.testing import CliRunner

from sqlitex.cli import app

RUNNER = CliRunner()


def _output(result) -> str:
    text = result.output or ""
    with contextlib.suppress(ValueError, AttributeError):
        text += result.stderr or ""
    return text


def _prepare(tmp_path):
    home = tmp_path / "home"
    db = tmp_path / "demo.sqlite"
    result = RUNNER.invoke(app, ["--home", str(home), "demo", str(db)])
    assert result.exit_code == 0, _output(result)
    result = RUNNER.invoke(app, ["--home", str(home), "init", str(db), "--no-ai", "--json"])
    assert result.exit_code == 0, _output(result)
    return home, db


def test_demo_and_init_json(tmp_path):
    home, db = _prepare(tmp_path)
    payload = json.loads(
        RUNNER.invoke(app, ["--home", str(home), "init", str(db), "--no-ai", "--json"]).output
    )
    assert payload["tables"] == 9  # 8 tables + 1 view
    assert payload["relationships"] == 8
    assert payload["index"]["documents"] == 10
    assert (home / payload["catalog"].split("/")[-1]).exists()


def test_schema_and_describe_json(tmp_path):
    home, db = _prepare(tmp_path)
    result = RUNNER.invoke(app, ["--home", str(home), "schema", "--db", str(db), "--json"])
    assert result.exit_code == 0, _output(result)
    catalog = json.loads(result.output)
    assert catalog["database_name"] == "demo"

    result = RUNNER.invoke(app, ["--home", str(home), "describe", "orders", "--db", str(db), "--json"])
    assert result.exit_code == 0, _output(result)
    table = json.loads(result.output)
    assert table["name"] == "orders"
    assert len(table["columns"]) == 6


def test_query_and_guard(tmp_path):
    home, db = _prepare(tmp_path)
    result = RUNNER.invoke(
        app,
        ["--home", str(home), "query", "SELECT COUNT(*) AS n FROM orders", "--db", str(db)],
    )
    assert result.exit_code == 0, _output(result)
    assert "160" in _output(result)

    result = RUNNER.invoke(app, ["--home", str(home), "query", "DELETE FROM orders", "--db", str(db)])
    assert result.exit_code == 1
    assert "Only read-only queries are allowed" in _output(result)


def test_status_and_config_json(tmp_path):
    home, db = _prepare(tmp_path)
    result = RUNNER.invoke(app, ["--home", str(home), "status", "--json"])
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.output)
    assert len(payload["catalogs"]) == 1
    assert payload["catalogs"][0]["database"] == str(db)

    result = RUNNER.invoke(app, ["--home", str(home), "config", "--json"])
    assert result.exit_code == 0, _output(result)
    config = json.loads(result.output)
    assert config["home"] == str(home)
    assert config["api_key_set"] is False


def test_reindex_json(tmp_path):
    home, db = _prepare(tmp_path)
    result = RUNNER.invoke(app, ["--home", str(home), "reindex", "--db", str(db), "--lexical", "--json"])
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.output)
    assert payload["documents"] == 10
    assert payload["vectors"] is False


def test_ask_without_ai_fails_cleanly(tmp_path):
    home, db = _prepare(tmp_path)
    result = RUNNER.invoke(
        app, ["--home", str(home), "ask", "How many customers are there?", "--db", str(db)]
    )
    assert result.exit_code == 1
    assert "AI is not configured" in _output(result)


def test_ask_with_fake_llm(tmp_path, monkeypatch, fake_llm):
    home, db = _prepare(tmp_path)
    monkeypatch.setenv("SQLITEX_API_BASE_URL", "http://localhost:9/v1")
    monkeypatch.setenv("SQLITEX_API_KEY", "test")
    monkeypatch.setenv("SQLITEX_MODEL", "fake")
    monkeypatch.setenv("SQLITEX_EMBEDDINGS", "off")
    fake = fake_llm(
        [
            {"mode": "sql", "sql": "SELECT COUNT(*) AS total FROM customers"},
            {"answer": "There are 45 customers.", "followups": []},
        ]
    )
    monkeypatch.setattr("sqlitex.cli.LLMClient", lambda settings: fake)

    result = RUNNER.invoke(
        app, ["--home", str(home), "ask", "How many customers are there?", "--db", str(db)]
    )
    assert result.exit_code == 0, _output(result)
    output = _output(result)
    assert "45 customers" in output
    assert "SELECT COUNT(*)" in output


def test_ask_json_output(tmp_path, monkeypatch, fake_llm):
    home, db = _prepare(tmp_path)
    monkeypatch.setenv("SQLITEX_API_BASE_URL", "http://localhost:9/v1")
    monkeypatch.setenv("SQLITEX_API_KEY", "test")
    monkeypatch.setenv("SQLITEX_MODEL", "fake")
    fake = fake_llm(
        [
            {"mode": "sql", "sql": "SELECT COUNT(*) AS total FROM customers"},
            {"answer": "There are 45 customers.", "followups": []},
        ]
    )
    monkeypatch.setattr("sqlitex.cli.LLMClient", lambda settings: fake)

    result = RUNNER.invoke(
        app,
        ["--home", str(home), "ask", "How many customers are there?", "--db", str(db), "--json"],
    )
    assert result.exit_code == 0, _output(result)
    payload = json.loads(result.output)
    assert payload["rows"] == [[45]]
    assert payload["answer"] == "There are 45 customers."
    assert payload["mode"] == "sql"


def test_mermaid_output(tmp_path):
    home, db = _prepare(tmp_path)
    result = RUNNER.invoke(app, ["--home", str(home), "schema", "--db", str(db), "--mermaid"])
    assert result.exit_code == 0, _output(result)
    assert "erDiagram" in _output(result)
    assert "orders" in _output(result)


def test_version():
    result = RUNNER.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "sqlitex" in _output(result).lower()
