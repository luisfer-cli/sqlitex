"""Persist catalogs and indexes under SQLITEX_HOME."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from sqlitex.config import Settings
from sqlitex.errors import CatalogError, DatabaseError
from sqlitex.models import Catalog
from sqlitex.utils import safe_slug

CATALOG_SUFFIX = ".catalog.json"
INDEX_SUFFIX = ".index.json"
VECTORS_SUFFIX = ".vectors.npy"


@dataclass(frozen=True)
class StoredCatalog:
    path: Path
    catalog: Catalog


def _db_hash(db_path: Path) -> str:
    return hashlib.sha1(str(db_path.resolve()).encode("utf-8")).hexdigest()[:10]


def catalog_path_for(db_path: Path, settings: Settings) -> Path:
    db_path = db_path.expanduser().resolve()
    return settings.home / f"{safe_slug(db_path.stem)}-{_db_hash(db_path)}{CATALOG_SUFFIX}"


def index_paths(catalog_path: Path) -> tuple[Path, Path]:
    stem = (
        catalog_path.name[: -len(CATALOG_SUFFIX)]
        if catalog_path.name.endswith(CATALOG_SUFFIX)
        else catalog_path.stem
    )
    return catalog_path.parent / f"{stem}{INDEX_SUFFIX}", catalog_path.parent / f"{stem}{VECTORS_SUFFIX}"


def save_catalog(catalog: Catalog, settings: Settings) -> Path:
    path = catalog_path_for(Path(catalog.database), settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = catalog.model_dump_json(indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_catalog(path: Path) -> Catalog:
    try:
        return Catalog.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"Catalog does not exist: {path}.") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise CatalogError(
            f"The catalog {path} is corrupt or incompatible. Run `sqlitex init` again."
        ) from exc


def load_catalog_for_db(db_path: Path, settings: Settings) -> Catalog | None:
    path = catalog_path_for(db_path, settings)
    return load_catalog(path) if path.exists() else None


def iter_catalog_files(settings: Settings) -> list[Path]:
    if not settings.home.is_dir():
        return []
    return sorted(settings.home.glob(f"*{CATALOG_SUFFIX}"))


def resolve_catalog(
    settings: Settings,
    db: Path | None = None,
) -> tuple[Path, Catalog]:
    """Find the catalog selected by --db, SQLITEX_DB, or the only one available."""

    target = db or settings.db
    if target is not None:
        path = catalog_path_for(target, settings)
        if not path.exists():
            raise CatalogError(f"No catalog found for “{target}”. Run first: sqlitex init {target}")
        return path, load_catalog(path)

    files = iter_catalog_files(settings)
    if not files:
        raise CatalogError(
            "There are no catalogs yet. Run `sqlitex init <file.sqlite>` or pass a database with --db."
        )
    if len(files) > 1:
        names: list[str] = []
        for path in files:
            try:
                names.append(load_catalog(path).database)
            except CatalogError:
                names.append(f"{path.name} (corrupt catalog)")
        listing = "\n".join(f"  · {name}" for name in names)
        raise CatalogError(
            "Multiple catalogs found in SQLITEX_HOME. Choose one with --db or SQLITEX_DB:\n" + listing
        )
    return files[0], load_catalog(files[0])


def resolve_db_path(settings: Settings, db: Path | None = None) -> Path:
    """Resolve the database path without requiring a catalog."""

    target = db or settings.db
    if target is None:
        files = iter_catalog_files(settings)
        if len(files) == 1:
            target = Path(load_catalog(files[0]).database)
    if target is None:
        raise DatabaseError("No database specified. Use --db, set SQLITEX_DB, or run `sqlitex init`.")
    target = target.expanduser().resolve()
    if not target.is_file():
        raise DatabaseError(f"Database file does not exist: {target}")
    return target
