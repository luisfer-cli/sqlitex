from __future__ import annotations

import json

import pytest

from sqlitex.catalog import (
    catalog_path_for,
    index_paths,
    iter_catalog_files,
    load_catalog,
    load_catalog_for_db,
    resolve_catalog,
    resolve_db_path,
    save_catalog,
)
from sqlitex.errors import CatalogError, DatabaseError


def test_save_and_load(catalog, settings):
    path = save_catalog(catalog, settings)
    assert path.exists()
    loaded = load_catalog(path)
    assert loaded.database == catalog.database
    assert loaded.fingerprint == catalog.fingerprint
    assert len(loaded.tables) == len(catalog.tables)


def test_catalog_path_is_stable(demo_db, settings):
    assert catalog_path_for(demo_db, settings) == catalog_path_for(demo_db, settings)
    assert catalog_path_for(demo_db, settings).name.endswith(".catalog.json")


def test_load_catalog_for_db(catalog, demo_db, settings, tmp_path):
    assert load_catalog_for_db(demo_db, settings) is None
    save_catalog(catalog, settings)
    loaded = load_catalog_for_db(demo_db, settings)
    assert loaded is not None
    assert loaded.database_name == "demo"
    assert load_catalog_for_db(tmp_path / "otra.sqlite", settings) is None


def test_resolve_single_catalog(catalog, demo_db, settings):
    save_catalog(catalog, settings)
    path, loaded = resolve_catalog(settings)
    assert loaded.database == str(demo_db)
    path2, loaded2 = resolve_catalog(settings, demo_db)
    assert path2 == path
    assert loaded2.fingerprint == loaded.fingerprint


def test_resolve_missing_catalog(settings, tmp_path):
    with pytest.raises(CatalogError, match="init"):
        resolve_catalog(settings, tmp_path / "sin-catalogo.sqlite")


def test_resolve_multiple_catalogs(catalog, settings, tmp_path):
    from sqlitex.demo import create_demo_database
    from sqlitex.introspect import introspect_database

    save_catalog(catalog, settings)
    other_db = create_demo_database(tmp_path / "otra.sqlite")
    other_catalog = introspect_database(other_db, settings)
    save_catalog(other_catalog, settings)

    assert len(iter_catalog_files(settings)) == 2
    with pytest.raises(CatalogError, match="Multiple catalogs"):
        resolve_catalog(settings)


def test_corrupt_catalog_raises(catalog, settings):
    path = save_catalog(catalog, settings)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CatalogError, match="corrupt"):
        load_catalog(path)


def test_index_paths(catalog, settings):
    path = save_catalog(catalog, settings)
    meta, vectors = index_paths(path)
    assert meta.name.endswith(".index.json")
    assert vectors.name.endswith(".vectors.npy")
    assert meta.parent == path.parent


def test_resolve_db_path_from_settings(demo_db, settings):
    from dataclasses import replace

    configured = replace(settings, db=demo_db)
    assert resolve_db_path(configured) == demo_db
    assert resolve_db_path(settings, demo_db) == demo_db


def test_resolve_db_path_from_single_catalog(catalog, demo_db, settings):
    save_catalog(catalog, settings)
    assert resolve_db_path(settings) == demo_db


def test_resolve_db_path_missing(settings):
    with pytest.raises(DatabaseError):
        resolve_db_path(settings)


def test_saved_catalog_is_valid_json(catalog, settings):
    path = save_catalog(catalog, settings)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["database_name"] == "demo"
    assert data["stats"]["tables"] == 8
