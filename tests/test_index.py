from __future__ import annotations

from dataclasses import replace

from sqlitex.catalog import index_paths, save_catalog
from sqlitex.index import build_documents, build_index, index_summary, load_index, retrieve


def _build(catalog, settings, fake, *, embeddings=False, vector_settings=None):
    path = save_catalog(catalog, settings)
    effective = vector_settings or settings
    index = build_index(
        catalog,
        path,
        effective,
        fake,
        use_embeddings=embeddings,
    )
    return path, index


def test_documents_cover_every_table(catalog):
    documents = build_documents(catalog)
    assert documents[0].id == "global"
    table_ids = {document.id for document in documents if document.table}
    assert table_ids == {f"table:{table.name}" for table in catalog.tables}
    orders = next(document for document in documents if document.table == "orders")
    assert "customers" in orders.text
    assert "status" in orders.text


def test_lexical_retrieval_finds_reviews(catalog, settings, fake_llm):
    _, index = _build(catalog, settings, fake_llm())
    results = retrieve(index, "reviews with the lowest rating", settings, fake_llm(), top_k=4)
    assert any(item.doc.table == "reviews" for item in results[:3])


def test_named_table_boost(catalog, settings, fake_llm):
    _, index = _build(catalog, settings, fake_llm())
    results = retrieve(index, "cancelled orders by channel", settings, fake_llm(), top_k=4)
    assert results[0].doc.table == "orders"


def test_retrieval_off_returns_all_tables(catalog, settings, fake_llm):
    _, index = _build(catalog, settings, fake_llm())
    off = replace(settings, retrieval="off")
    results = retrieve(index, "anything", off, None)
    assert len(results) == len(catalog.tables)


def test_small_schema_includes_all_tables(catalog, settings, fake_llm):
    _, index = _build(catalog, settings, fake_llm())
    wide = replace(settings, all_tables_threshold=100)
    results = retrieve(index, "customers", wide, fake_llm())
    assert len(results) == len(catalog.tables)


def test_index_roundtrip_lexical(catalog, settings, fake_llm):
    path, index = _build(catalog, settings, fake_llm())
    meta_path, _ = index_paths(path)
    assert meta_path.exists()
    assert index.vector_ready is False
    loaded = load_index(path, catalog, settings)
    assert loaded is not None
    assert not loaded.vector_ready
    assert len(loaded.docs) == len(index.docs)
    assert "lexical" in index_summary(loaded)


def test_index_with_embeddings_roundtrip(catalog, settings, fake_llm):
    vector_settings = replace(
        settings,
        embeddings="on",
        embed_model="fake-embed",
        embed_base_url="http://localhost:9/v1",
        embed_api_key="test",
    )
    fake = fake_llm()
    path, index = _build(
        catalog,
        settings,
        fake,
        embeddings=True,
        vector_settings=vector_settings,
    )
    assert index.vector_ready is True
    meta_path, vectors_path = index_paths(path)
    assert meta_path.exists()
    assert vectors_path.exists()
    loaded = load_index(path, catalog, vector_settings)
    assert loaded is not None
    assert loaded.vector_ready is True
    results = retrieve(loaded, "cancelled orders", vector_settings, fake, top_k=3)
    assert results
    assert fake.embed_calls


def test_stale_index_is_ignored(catalog, settings, fake_llm):
    path, _ = _build(catalog, settings, fake_llm())
    stale_catalog = catalog.model_copy(update={"fingerprint": "other"})
    assert load_index(path, stale_catalog, settings) is None


def test_missing_index_returns_none(catalog, settings, tmp_path):
    assert load_index(tmp_path / "missing.catalog.json", catalog, settings) is None
