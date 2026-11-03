"""RAG index: catalog documents, embeddings, and hybrid retrieval."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from sqlitex.catalog import index_paths
from sqlitex.config import Settings
from sqlitex.errors import LLMError
from sqlitex.llm import LLMClient
from sqlitex.models import Catalog, TableInfo
from sqlitex.utils import normalize_words, sha256_text, truncate

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_LEXICAL_WEIGHT = 0.45
_VECTOR_WEIGHT = 0.55
_NAME_BOOST = 0.35


class Document(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    table: str | None = None
    kind: str = "table"
    text: str
    hash: str = ""


class IndexMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int = 1
    fingerprint: str = ""
    model: str | None = None
    built_at: str = ""
    embedded: bool = False
    dim: int = 0
    docs: list[Document] = Field(default_factory=list)


@dataclass
class IndexData:
    docs: list[Document]
    vectors: np.ndarray | None
    model: str | None
    fingerprint: str
    built_at: str

    @property
    def vector_ready(self) -> bool:
        return self.vectors is not None and len(self.vectors) == len(self.docs)


@dataclass
class Retrieved:
    doc: Document
    score: float
    lexical: float = 0.0
    vector: float = 0.0


def _table_document(table: TableInfo, catalog: Catalog) -> Document:
    ai = table.ai
    lines = [
        f"TABLE {table.name} ({table.kind}; rows: {table.row_count if table.row_count is not None else '?'})",
    ]
    if ai:
        if ai.summary:
            lines.append(f"Summary: {ai.summary}")
        if ai.purpose:
            lines.append(f"Purpose: {ai.purpose}")
        if ai.entity:
            lines.append(f"Entity: {ai.entity}")
        if ai.tags:
            lines.append(f"Tags: {', '.join(ai.tags)}")
    lines.append("Columns:")
    for column in table.columns:
        flags = []
        if column.primary_key:
            flags.append("PK")
        if column.foreign_key:
            flags.append(f"FK {column.foreign_key}")
        header = column.name
        if column.declared_type:
            header += f" {column.declared_type}"
        if flags:
            header += f" [{', '.join(flags)}]"
        meaning = ""
        if ai:
            info = _ai_column(ai, column.name)
            if info and info.meaning:
                meaning = f": {info.meaning}"
        lines.append(f"- {header}{meaning}")
        if column.profile and column.profile.top_values:
            values = ", ".join(f"{item['value']} ({item['count']})" for item in column.profile.top_values[:5])
            lines.append(f"  frequent values: {values}")

    relations = [
        f"{rel.from_table}.{rel.from_column} → {rel.to_table}.{rel.to_column} ({rel.kind})"
        for rel in catalog.relationships
        if table.name in {rel.from_table, rel.to_table}
    ]
    if relations:
        lines.append("Relationships: " + "; ".join(relations[:15]))
    if table.sql:
        lines.append(f"DDL: {truncate(table.sql, 1200)}")
    if table.sample_rows:
        lines.append("Sample: " + truncate(str(table.sample_rows[:2]), 900))
    if ai and ai.example_questions:
        lines.append("Example questions: " + " | ".join(ai.example_questions[:5]))
    if ai and ai.data_quality_notes:
        lines.append("Data quality notes: " + " | ".join(ai.data_quality_notes[:5]))

    text = "\n".join(lines)
    return Document(
        id=f"table:{table.name}",
        table=table.name,
        kind="table" if table.kind == "table" else "view",
        text=text,
        hash=sha256_text(text),
    )


def _global_document(catalog: Catalog) -> Document:
    ai = catalog.global_ai
    lines = [f"DATABASE {catalog.database_name}"]
    if ai:
        if ai.summary:
            lines.append(f"Summary: {ai.summary}")
        if ai.domain:
            lines.append(f"Domain: {ai.domain}")
        if ai.main_entities:
            lines.append("Main entities: " + ", ".join(ai.main_entities))
        if ai.key_questions:
            lines.append("Key questions: " + " | ".join(ai.key_questions[:8]))
        if ai.notes:
            lines.append("Notes: " + " | ".join(ai.notes[:5]))
    lines.append(
        "Tables: "
        + ", ".join(
            f"{table.name} ({table.row_count if table.row_count is not None else '?'} rows)"
            for table in catalog.tables
        )
    )
    for rel in catalog.relationships[:30]:
        lines.append(
            f"Relationship: {rel.from_table}.{rel.from_column} → {rel.to_table}.{rel.to_column} ({rel.kind})"
        )
    text = "\n".join(lines)
    return Document(
        id="global",
        table=None,
        kind="global",
        text=text,
        hash=sha256_text(text),
    )


def _ai_column(ai, name: str):
    if name in ai.columns:
        return ai.columns[name]
    lowered = name.lower()
    for key, value in ai.columns.items():
        if key.lower() == lowered:
            return value
    return None


def build_documents(catalog: Catalog) -> list[Document]:
    """Turn the catalog into text documents ready for RAG."""

    return [_global_document(catalog), *[_table_document(table, catalog) for table in catalog.tables]]


def _normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


def save_index(index: IndexData, catalog_path: Path) -> None:
    meta_path, vectors_path = index_paths(catalog_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta = IndexMeta(
        fingerprint=index.fingerprint,
        model=index.model,
        built_at=index.built_at,
        embedded=index.vectors is not None,
        dim=int(index.vectors.shape[1]) if index.vectors is not None and index.vectors.size else 0,
        docs=index.docs,
    )
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    tmp.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(meta_path)
    if index.vectors is not None:
        np.save(vectors_path, index.vectors)
    elif vectors_path.exists():
        vectors_path.unlink()


def load_index(catalog_path: Path, catalog: Catalog, settings: Settings) -> IndexData | None:
    meta_path, vectors_path = index_paths(catalog_path)
    if not meta_path.exists():
        return None
    try:
        meta = IndexMeta.model_validate_json(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not meta.docs or meta.fingerprint != catalog.fingerprint:
        return None

    vectors: np.ndarray | None = None
    if meta.embedded and vectors_path.exists() and meta.model == settings.embed_model:
        try:
            loaded = np.load(vectors_path)
            if loaded.ndim == 2 and loaded.shape[0] == len(meta.docs):
                vectors = loaded.astype(np.float32)
        except (OSError, ValueError):
            vectors = None

    return IndexData(
        docs=meta.docs,
        vectors=vectors,
        model=meta.model,
        fingerprint=meta.fingerprint,
        built_at=meta.built_at,
    )


def build_index(
    catalog: Catalog,
    catalog_path: Path,
    settings: Settings,
    client: LLMClient,
    *,
    use_embeddings: bool = True,
    on_message: Callable[[str], None] | None = None,
) -> IndexData:
    """Build and save the index. If embeddings fail, fall back to text only."""

    notify = on_message or (lambda _message: None)
    docs = build_documents(catalog)
    vectors: np.ndarray | None = None
    model: str | None = None

    if use_embeddings and settings.embeddings_available:
        try:
            raw = client.embed([doc.text for doc in docs])
            matrix = np.asarray(raw, dtype=np.float32)
            if matrix.ndim != 2 or matrix.shape[0] != len(docs):
                raise LLMError("The returned embeddings have an unexpected shape.")
            vectors = _normalize_matrix(matrix)
            model = settings.embed_model
        except LLMError as exc:
            notify(
                "Could not generate embeddings; falling back to lexical search (BM25). "
                + truncate(str(exc), 240)
            )

    index = IndexData(
        docs=docs,
        vectors=vectors,
        model=model,
        fingerprint=catalog.fingerprint,
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    save_index(index, catalog_path)
    return index


def _tokens(text: str) -> set[str]:
    words = _TOKEN_RE.findall(normalize_words(text))
    result: set[str] = set()
    for word in words:
        if len(word) < 2:
            continue
        result.add(word)
        if "_" in word:
            result.update(part for part in word.split("_") if len(part) > 1 and part != "id")
        if len(word) > 3:
            if word.endswith("es"):
                result.add(word[:-2])
            elif word.endswith("s"):
                result.add(word[:-1])
    return result


class _BM25:
    def __init__(self, docs: Iterable[Document]) -> None:
        self.tfs: list[Counter[str]] = []
        self.lengths: list[int] = []
        self.df: Counter[str] = Counter()
        for doc in docs:
            tokens = _tokens(doc.text)
            counter = Counter(tokens)
            self.tfs.append(counter)
            self.lengths.append(sum(counter.values()))
            self.df.update(counter.keys())
        self.total = len(self.tfs)
        self.avgdl = sum(self.lengths) / self.total if self.total else 0.0

    def scores(self, query_tokens: set[str]) -> list[float]:
        if not self.total or not query_tokens:
            return [0.0] * self.total
        k1, b = 1.5, 0.75
        output: list[float] = []
        for counter, length in zip(self.tfs, self.lengths, strict=True):
            score = 0.0
            for token in query_tokens:
                freq = counter.get(token, 0)
                if not freq:
                    continue
                idf = math.log(1 + (self.total - self.df[token] + 0.5) / (self.df[token] + 0.5))
                denominator = freq + k1 * (1 - b + b * (length / self.avgdl if self.avgdl else 0))
                score += idf * (freq * (k1 + 1)) / denominator
            output.append(score)
        return output


def _normalize_scores(scores: list[float]) -> list[float]:
    highest = max(scores, default=0.0)
    if highest <= 0:
        return [0.0] * len(scores)
    return [score / highest for score in scores]


def _question_matches_table(question_tokens: set[str], table_name: str) -> bool:
    parts = [part for part in re.split(r"[^a-z0-9]+", normalize_words(table_name)) if len(part) > 1]
    if not parts:
        return False
    for part in parts:
        forms = {part, part[:-1] if part.endswith("s") and len(part) > 3 else part}
        if part.endswith("es") and len(part) > 4:
            forms.add(part[:-2])
        if not forms & question_tokens:
            return False
    return True


def retrieve(
    index: IndexData,
    question: str,
    settings: Settings,
    client: LLMClient | None = None,
    *,
    top_k: int | None = None,
) -> list[Retrieved]:
    """Retrieve the most relevant documents (lexical + vector)."""

    table_docs = [doc for doc in index.docs if doc.table]
    k = top_k or settings.top_k

    if settings.retrieval == "off":
        return [Retrieved(doc=doc, score=1.0) for doc in table_docs]
    if len(table_docs) <= settings.all_tables_threshold:
        return [Retrieved(doc=doc, score=1.0) for doc in table_docs]

    query_tokens = _tokens(question)
    lexical = _BM25(index.docs).scores(query_tokens)
    lexical_norm = _normalize_scores(lexical)

    vector_norm: list[float] | None = None
    if index.vector_ready and settings.retrieval in {"hybrid", "vector"} and client is not None:
        try:
            query_vector = np.asarray(client.embed([question])[0], dtype=np.float32)
            norm = float(np.linalg.norm(query_vector))
            if norm > 0 and index.vectors is not None and index.vectors.shape[1] == query_vector.shape[0]:
                vector_norm = _normalize_scores(
                    [float(value) for value in (index.vectors @ (query_vector / norm))]
                )
        except LLMError:
            vector_norm = None

    results: list[Retrieved] = []
    for position, doc in enumerate(index.docs):
        lex = lexical_norm[position]
        vec = vector_norm[position] if vector_norm is not None else 0.0
        if settings.retrieval == "vector" and vector_norm is not None:
            score = vec
        elif settings.retrieval == "lexical" or vector_norm is None:
            score = lex
        else:
            score = _LEXICAL_WEIGHT * lex + _VECTOR_WEIGHT * vec
        if doc.table and _question_matches_table(query_tokens, doc.table):
            score += _NAME_BOOST
        results.append(Retrieved(doc=doc, score=score, lexical=lex, vector=vec))

    results.sort(key=lambda item: item.score, reverse=True)
    return results[: max(k, 1)]


def index_summary(index: IndexData) -> str:
    mode = f"vector ({index.model})" if index.vector_ready else "lexical (BM25)"
    return f"{len(index.docs)} documents · {mode} search · {index.built_at}"
