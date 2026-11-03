"""Catalog data models and AI response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ValueProfile(BaseModel):
    """Approximate profile of a column, computed from a sample."""

    model_config = ConfigDict(extra="ignore")

    sample_size: int = 0
    non_null: int | None = None
    null_ratio: float | None = None
    distinct: int | None = None
    min: Any | None = None
    max: Any | None = None
    top_values: list[dict[str, Any]] = Field(default_factory=list)


class ColumnInfo(BaseModel):
    name: str
    declared_type: str = ""
    notnull: bool = False
    default: str | None = None
    primary_key: bool = False
    foreign_key: str | None = None
    profile: ValueProfile | None = None


class ForeignKeyInfo(BaseModel):
    column: str
    ref_table: str
    ref_column: str
    on_update: str | None = None
    on_delete: str | None = None
    group: int | None = None


class IndexInfo(BaseModel):
    name: str
    unique: bool = False
    origin: str | None = None
    partial: bool = False
    columns: list[str] = Field(default_factory=list)
    sql: str | None = None


class TriggerInfo(BaseModel):
    name: str
    table: str | None = None
    sql: str | None = None


class AIColumnInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meaning: str = ""
    notes: str | None = None
    pii: bool | None = None


class AITableInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    purpose: str | None = None
    entity: str | None = None
    columns: dict[str, AIColumnInfo] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    example_questions: list[str] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)


class TableInfo(BaseModel):
    name: str
    kind: Literal["table", "view", "virtual"] = "table"
    sql: str | None = None
    columns: list[ColumnInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)
    row_count: int | None = None
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    ai: AITableInfo | None = None
    schema_hash: str = ""
    notes: list[str] = Field(default_factory=list)


class Relationship(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    kind: Literal["declared", "inferred"] = "declared"
    description: str | None = None


class GlobalAI(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    domain: str | None = None
    main_entities: list[str] = Field(default_factory=list)
    key_questions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    sensitivity_notes: list[str] = Field(default_factory=list)


class CatalogStats(BaseModel):
    tables: int = 0
    views: int = 0
    virtual_tables: int = 0
    rows: int | None = None
    relationships: int = 0
    triggers: int = 0
    size_bytes: int | None = None


class Catalog(BaseModel):
    version: int = 1
    database: str
    database_name: str
    generated_at: str
    sqlite_version: str | None = None
    stats: CatalogStats = Field(default_factory=CatalogStats)
    pragmas: dict[str, Any] = Field(default_factory=dict)
    tables: list[TableInfo] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    triggers: list[TriggerInfo] = Field(default_factory=list)
    global_ai: GlobalAI | None = None
    warnings: list[str] = Field(default_factory=list)
    ai_enabled: bool = False
    ai_model: str | None = None
    language: str = "es"
    fingerprint: str = ""


class SqlPlan(BaseModel):
    """Plan returned by the model before querying the data."""

    model_config = ConfigDict(extra="ignore")

    mode: Literal["sql", "catalog"] = "sql"
    sql: str | None = None
    rationale: str | None = None
    answer: str | None = None


class AnswerPayload(BaseModel):
    """Final answer written by the model."""

    model_config = ConfigDict(extra="ignore")

    answer: str = ""
    followups: list[str] = Field(default_factory=list)
