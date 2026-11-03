from __future__ import annotations

import re
from dataclasses import replace

from conftest import FakeLLM

from sqlitex.enrich import enrich_catalog
from sqlitex.errors import LLMError
from sqlitex.models import AITableInfo, GlobalAI


class EnrichFake(FakeLLM):
    def chat_json(self, system: str, user: str, schema, **kwargs):
        self.calls.append({"system": system, "user": user, "schema": schema, **kwargs})
        if schema is GlobalAI:
            return GlobalAI(
                summary="Global summary of the store.",
                domain="E-commerce",
                main_entities=["Customers", "Orders", "Products"],
                key_questions=["How much did we sell?"],
                notes=["Synthetic data"],
                sensitivity_notes=["Emails and names"],
            )
        match = re.search(r"Table/view: (.+)", user)
        name = match.group(1).strip() if match else "unknown"
        return AITableInfo(
            summary=f"Summary of {name}",
            purpose=f"Purpose of {name}",
            entity="One row",
            columns={},
            tags=["demo"],
            example_questions=[f"How many records are in {name}?"],
            data_quality_notes=["Test note"],
        )


def test_enrich_all_tables(catalog, settings):
    fake = EnrichFake()
    progress: list[tuple[int, int, str]] = []
    report = enrich_catalog(
        catalog,
        settings,
        fake,
        force=True,
        on_progress=lambda done, total, label: progress.append((done, total, label)),
    )

    assert report.enabled is True
    assert report.enriched == len(catalog.tables)
    assert report.reused == 0
    assert report.global_done is True
    assert report.failed == []
    assert catalog.ai_enabled is True
    assert catalog.ai_model == settings.model
    assert catalog.global_ai is not None
    assert all(table.ai is not None for table in catalog.tables)
    assert any("customers" in (table.ai.summary or "") for table in catalog.tables)
    assert progress
    assert progress[-1][0] == progress[-1][1]


def test_enrich_reuses_cached_annotations(catalog, settings):
    first = EnrichFake()
    enrich_catalog(catalog, settings, first, force=True)

    second = EnrichFake()
    report = enrich_catalog(catalog, settings, second, force=False, previous=catalog)
    assert report.enriched == 0
    assert report.reused == len(catalog.tables)
    assert report.global_done is True
    # Only the global summary triggers another model call.
    assert len(second.calls) == 1
    assert second.calls[0]["schema"] is GlobalAI


def test_enrich_force_ignores_cache(catalog, settings):
    enrich_catalog(catalog, settings, EnrichFake(), force=True)
    fake = EnrichFake()
    report = enrich_catalog(catalog, settings, fake, force=True, previous=catalog)
    assert report.enriched == len(catalog.tables)
    assert report.reused == 0


def test_enrich_respects_max_tables(catalog, settings):
    limited = replace(settings, enrich_max_tables=2)
    report = enrich_catalog(catalog, limited, EnrichFake(), force=True)
    assert report.enriched == 2
    assert len(report.skipped) == len(catalog.tables) - 2
    assert any("SQLITEX_ENRICH_MAX_TABLES" in warning for warning in catalog.warnings)


def test_enrich_reports_failures(catalog, settings):
    class FailingFake(FakeLLM):
        def chat_json(self, *args, **kwargs):
            raise LLMError("model unavailable")

    fake = FailingFake()
    report = enrich_catalog(catalog, settings, fake, force=True)
    assert report.enriched == 0
    assert len(report.failed) == len(catalog.tables)
    assert report.global_done is False
    assert any("AI annotations failed" in warning for warning in catalog.warnings)


def test_enrich_without_ai(catalog, settings):
    fake = FakeLLM()
    fake.available = False
    report = enrich_catalog(catalog, settings, fake, force=True)
    assert report.enabled is False
    assert report.error is not None
    assert fake.calls == []
    assert all(table.ai is None for table in catalog.tables)
