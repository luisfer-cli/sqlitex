from __future__ import annotations

from sqlitex.ask import HistoryTurn, answer_question
from sqlitex.catalog import save_catalog
from sqlitex.errors import LLMError, SqlitexError
from sqlitex.index import IndexData, build_index
from sqlitex.models import AnswerPayload, Catalog, SqlPlan

PLAN = {"mode": "sql", "sql": "SELECT COUNT(*) AS total FROM customers", "rationale": "Counts rows."}
ANSWER = {"answer": "There are 45 customers.", "followups": ["How many are businesses?"]}


def _prepare(catalog, settings, fake):
    path = save_catalog(catalog, settings)
    index = build_index(catalog, path, settings, fake, use_embeddings=False)
    return index


def _ask(catalog, index, settings, fake, demo_db, question="How many customers are there?", **kwargs):
    return answer_question(
        question,
        catalog=catalog,
        index=index,
        db_path=demo_db,
        client=fake,
        settings=settings,
        **kwargs,
    )


def test_answer_sql_flow(catalog, settings, fake_llm, demo_db):
    fake = fake_llm([PLAN, ANSWER])
    index = _prepare(catalog, settings, fake)
    result = _ask(catalog, index, settings, fake, demo_db)

    assert result.error is None
    assert result.mode == "sql"
    assert result.rows == [[45]]
    assert result.answer == "There are 45 customers."
    assert result.attempts == 1
    assert result.followups == ["How many are businesses?"]
    assert len(fake.calls) == 2
    assert fake.calls[0]["schema"] is SqlPlan
    assert "customers" in fake.calls[0]["user"]
    assert fake.calls[1]["schema"] is AnswerPayload


def test_sql_is_repaired_after_error(catalog, settings, fake_llm, demo_db):
    fake = fake_llm(
        [
            {"mode": "sql", "sql": "SELECT missing_column FROM orders"},
            {"mode": "sql", "sql": "SELECT COUNT(*) AS total FROM orders"},
            ANSWER,
        ]
    )
    index = _prepare(catalog, settings, fake)
    result = _ask(catalog, index, settings, fake, demo_db, question="How many orders are there?")

    assert result.error is None
    assert result.attempts == 2
    assert result.rows == [[160]]
    assert "REQUIRED CORRECTION" in fake.calls[1]["user"]
    assert "missing_column" in fake.calls[1]["user"]


def test_write_plan_is_rejected_and_repaired(catalog, settings, fake_llm, demo_db):
    fake = fake_llm(
        [
            {"mode": "sql", "sql": "DELETE FROM orders"},
            {"mode": "sql", "sql": "SELECT COUNT(*) AS total FROM orders"},
            ANSWER,
        ]
    )
    index = _prepare(catalog, settings, fake)
    result = _ask(catalog, index, settings, fake, demo_db)

    assert result.error is None
    assert result.attempts == 2
    assert "Only read-only queries are allowed" in fake.calls[1]["user"]


def test_catalog_mode_does_not_execute_sql(catalog, settings, fake_llm, demo_db):
    fake = fake_llm([{"mode": "catalog", "answer": "The database stores customers, orders, and products."}])
    index = _prepare(catalog, settings, fake)
    result = _ask(catalog, index, settings, fake, demo_db, question="What data is available?")

    assert result.error is None
    assert result.mode == "catalog"
    assert result.sql is None
    assert "customers" in result.answer
    assert len(fake.calls) == 1


def test_synthesis_failure_falls_back(catalog, settings, fake_llm, demo_db):
    fake = fake_llm([PLAN, LLMError("quota exhausted")])
    index = _prepare(catalog, settings, fake)
    result = _ask(catalog, index, settings, fake, demo_db)

    assert result.error is None
    assert result.rows == [[45]]
    assert "1 row" in result.answer
    assert result.followups == []


def test_history_is_sent_to_the_planner(catalog, settings, fake_llm, demo_db):
    fake = fake_llm([PLAN, ANSWER])
    index = _prepare(catalog, settings, fake)
    history = [HistoryTurn(question="How many customers are there?", sql="SELECT 1", answer="45")]
    result = _ask(catalog, index, settings, fake, demo_db, question="And how many orders?", history=history)

    assert result.error is None
    assert "Recent history" in fake.calls[0]["user"]
    assert "How many customers are there?" in fake.calls[0]["user"]


def test_empty_catalog_returns_error(settings, fake_llm, demo_db):
    empty = Catalog(
        database=str(demo_db),
        database_name="empty",
        generated_at="2026-01-01T00:00:00+00:00",
    )
    index = IndexData(
        docs=[],
        vectors=None,
        model=None,
        fingerprint=empty.fingerprint,
        built_at="2026-01-01T00:00:00+00:00",
    )
    fake = fake_llm()
    result = _ask(empty, index, settings, fake, demo_db)
    assert result.error is not None
    assert fake.calls == []


def test_empty_question_raises(catalog, settings, fake_llm, demo_db):
    fake = fake_llm([])
    index = _prepare(catalog, settings, fake)
    try:
        _ask(catalog, index, settings, fake, demo_db, question="   ")
    except SqlitexError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("Expected SqlitexError")


def test_plan_failure_returns_error(catalog, settings, fake_llm, demo_db):
    fake = fake_llm([LLMError("provider down")])
    index = _prepare(catalog, settings, fake)
    result = _ask(catalog, index, settings, fake, demo_db)
    assert result.error is not None
    assert "provider down" in result.error
