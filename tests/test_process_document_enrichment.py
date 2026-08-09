from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook
from ai.models import ProductsList
from shared.observability import bind_observability_context
from application.ports.document_layout_port import DocumentAnchor, DocumentLayout
from application.services.pdf_evidence_index import SourceEvidenceValidationFailed

import application.use_cases.processing.process_document as process_document_uc


def _valid_xlsx_bytes() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Title", "Vendor"])
    worksheet.append(["Demo Product", "Source Vendor"])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class _FakeRuns:
    def __init__(self) -> None:
        self.created_updates: list[tuple[str, dict]] = []
        self.finalized: list[tuple[str, dict]] = []
        self.events: list[tuple[str, dict, int]] = []
        self.messages: list[tuple[str, str, str, int, dict | None]] = []

    def create_or_update_run(self, run_id: str, fields: dict) -> None:
        self.created_updates.append((run_id, dict(fields)))

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        duration_ms: int | None = None,
        error: str | None = None,
        extra_fields: dict | None = None,
    ) -> None:
        self.finalized.append(
            (
                run_id,
                {
                    "status": status,
                    "duration_ms": duration_ms,
                    "error": error,
                    "extra_fields": dict(extra_fields or {}),
                },
            )
        )

    def append_run_event(self, run_id: str, event: dict, seq: int) -> None:
        self.events.append((run_id, dict(event), seq))

    def append_run_message(
        self,
        run_id: str,
        *,
        role: str,
        message: str,
        seq: int,
        meta: dict | None = None,
    ) -> None:
        self.messages.append((run_id, role, message, seq, meta))


class _FakeLLMConfigs:
    def __init__(self, config: dict | None) -> None:
        self._config = config

    def get_active_llm_model_config(self, shop_domain: str) -> dict | None:
        _ = shop_domain
        return self._config


class _FakeSupabase:
    def __init__(self, model_config: dict | None = None) -> None:
        self.runs = _FakeRuns()
        self.llm_configs = _FakeLLMConfigs(model_config)
        self.file = _FakeFileStorage()


class _FakeFileStorage:
    def __init__(self) -> None:
        self.saved_files: list[dict] = []

    def save_file(
        self,
        file_id: str,
        name: str,
        content: bytes,
        *,
        content_type: str,
        file_origin: str,
        shop_domain: str | None = None,
    ) -> None:
        self.saved_files.append(
            {
                "file_id": file_id,
                "name": name,
                "content": content,
                "content_type": content_type,
                "file_origin": file_origin,
                "shop_domain": shop_domain,
            }
        )


class _FakeTracing:
    def __init__(self) -> None:
        self.completed_runs: list[str] = []
        self.events: list[dict] = []

    def emit_run_event(
        self,
        run_id: str,
        *,
        phase: str,
        message: str,
        level: str = "info",
        payload_preview=None,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        event = {
            "run_id": run_id,
            "phase": phase,
            "message": message,
            "level": level,
            "payload_preview": payload_preview,
            "error": error,
            "metadata": metadata,
        }
        self.events.append(event)
        return event

    def complete_run(self, run_id: str) -> None:
        self.completed_runs.append(run_id)


class _FakeLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_kwargs: dict = {}

    async def run_excel_agent_workflow(self, *args, **kwargs):
        _ = args
        self.last_kwargs = dict(kwargs)
        return dict(self.payload)


class _FakeLLMProductsList:
    async def run_excel_agent_workflow(self, *args, **kwargs):
        _ = args, kwargs
        return ProductsList(products=[{"title": "Demo Product"}])


class _SequenceLLM:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    async def run_excel_agent_workflow(self, *args, **kwargs):
        _ = args
        self.calls.append(dict(kwargs))
        return self.payloads.pop(0)


class _PdfLayoutService:
    def __init__(self) -> None:
        self.layout = DocumentLayout(
            1,
            (
                DocumentAnchor(
                    "azure-title",
                    "canonical.pdf",
                    1,
                    (0.1, 0.2, 0.8, 0.3),
                    "lassic Cotton T-Shirt",
                    "line",
                    "azure",
                ),
            ),
            "azure",
            (1,),
            (1,),
        )

    async def analyze_pdf(self, content: bytes, source_file_id: str) -> DocumentLayout:
        _ = content, source_file_id
        return self.layout


def _minimal_pdf_bytes() -> bytes:
    import fitz

    document = fitz.open()
    document.new_page()
    try:
        return document.tobytes()
    finally:
        document.close()


def _patch_canonical_pdf(monkeypatch, content: bytes) -> None:
    async def fake_ensure_canonical_pdf(**kwargs):
        _ = kwargs
        return SimpleNamespace(
            file_id="canonical-file",
            content=content,
            filename="canonical.pdf",
            content_type="application/pdf",
            cache_hit=False,
        )

    monkeypatch.setattr(process_document_uc, "ensure_canonical_pdf", fake_ensure_canonical_pdf)


@pytest.mark.asyncio
async def test_process_document_enriches_products_during_import(monkeypatch):
    async def fake_generate_suggestions_execute(
        *,
        supabase,
        products,
        shop_domain,
        normalization_settings=None,
        trace_event=None,
    ):
        _ = supabase, shop_domain, normalization_settings, trace_event
        assert products[0]["title"] == "Demo Product"
        return [
            {
                "product_index": 0,
                "patch_payload": {"vendor": "Enriched Vendor"},
                "value_origin": "ai_assisted",
            }
        ]

    monkeypatch.setattr(
        process_document_uc,
        "generate_suggestions_execute",
        fake_generate_suggestions_execute,
        raising=False,
    )

    supabase = _FakeSupabase(
        model_config={
            "base_url": "http://localhost:11434/v1/",
            "model_id": "deepseek-r1:8b",
            "api_key": "secret",
            "provider": "ollama",
        }
    )
    llm = _FakeLLM(payload={"products": [{"title": "Demo Product"}]})
    tracing = _FakeTracing()

    result = await process_document_uc.execute(
        supabase=supabase,
        llm=llm,
        tracing=tracing,
        ctx=SimpleNamespace(),
        file_bytes=_valid_xlsx_bytes(),
        input_name="catalog.xlsx",
        input_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        run_id="run-import-enrich",
        shop_domain="store.myshopify.com",
    )

    assert result["result"]["products"][0]["vendor"] == "Enriched Vendor"
    assert result["result"]["enrichment_applied"] is True
    assert result["result"]["enrichment_suggestions_count"] == 1
    assert result["result"]["products"][0]["value_origins"]["vendor"] == "ai_assisted"


@pytest.mark.asyncio
async def test_process_document_skips_suggestions_when_ai_enhancements_disabled(monkeypatch):
    async def fail_generate_suggestions_execute(**kwargs):
        _ = kwargs
        raise AssertionError("disabled processing must not generate suggestions")

    monkeypatch.setattr(
        process_document_uc,
        "generate_suggestions_execute",
        fail_generate_suggestions_execute,
    )
    result = await process_document_uc.execute(
        supabase=_FakeSupabase(),
        llm=_FakeLLM(payload={"products": [{"title": "Demo Product"}]}),
        tracing=_FakeTracing(),
        ctx=SimpleNamespace(),
        file_bytes=_valid_xlsx_bytes(),
        input_name="catalog.xlsx",
        input_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        shop_domain="store.myshopify.com",
        enable_ai_enhancements=False,
    )

    assert result["result"]["products"][0]["title"] == "Demo Product"
    assert "vendor" not in result["result"]["products"][0]
    assert result["result"]["products"][0]["value_origins"]["title"] == "source"
    assert result["result"]["enrichment_attempted"] is False


@pytest.mark.asyncio
async def test_process_document_normalizes_products_list_result_shape():
    supabase = _FakeSupabase()
    llm = _FakeLLMProductsList()
    tracing = _FakeTracing()

    result = await process_document_uc.execute(
        supabase=supabase,
        llm=llm,
        tracing=tracing,
        ctx=SimpleNamespace(),
        file_bytes=_valid_xlsx_bytes(),
        input_name="catalog.xlsx",
        input_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        run_id="run-import-products-list",
        shop_domain=None,
        write_to_file=False,
    )

    assert isinstance(result["result"], dict)
    assert result["result"]["products"][0]["title"] == "Demo Product"


@pytest.mark.asyncio
async def test_process_document_refreshes_workbook_output_after_enrichment(monkeypatch):
    async def fake_generate_suggestions_execute(
        *,
        supabase,
        products,
        shop_domain,
        normalization_settings=None,
        trace_event=None,
    ):
        _ = supabase, products, shop_domain, normalization_settings, trace_event
        return [
            {
                "product_index": 0,
                "patch_payload": {"vendor": "Enriched Vendor"},
                "value_origin": "ai_assisted",
            }
        ]

    monkeypatch.setattr(
        process_document_uc,
        "generate_suggestions_execute",
        fake_generate_suggestions_execute,
        raising=False,
    )

    supabase = _FakeSupabase(
        model_config={
            "base_url": "http://localhost:11434/v1/",
            "model_id": "deepseek-r1:8b",
            "api_key": "secret",
            "provider": "ollama",
        }
    )
    llm = _FakeLLM(
        payload={
            "file_id": "pre-enrichment-file",
            "filename": "catalog-products.xlsx",
            "products": [{"title": "Demo Product"}],
        }
    )
    tracing = _FakeTracing()

    result = await process_document_uc.execute(
        supabase=supabase,
        llm=llm,
        tracing=tracing,
        ctx=SimpleNamespace(),
        file_bytes=_valid_xlsx_bytes(),
        input_name="catalog.xlsx",
        input_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        run_id="run-import-refresh-output",
        shop_domain="store.myshopify.com",
        write_to_file=True,
    )

    payload = result["result"]
    assert payload["products"][0]["vendor"] == "Enriched Vendor"
    assert payload["file_id"] != "pre-enrichment-file"
    assert len(supabase.file.saved_files) == 1
    saved = supabase.file.saved_files[0]
    assert saved["file_id"] == payload["file_id"]
    assert saved["name"] == payload["filename"]
    finalized_extra = supabase.runs.finalized[-1][1]["extra_fields"]
    assert finalized_extra["output_file_id"] == payload["file_id"]
    assert finalized_extra["output_filename"] == payload["filename"]
    workbook = load_workbook(BytesIO(saved["content"]))
    worksheet = workbook["Products"]
    headers = {str(cell.value): index + 1 for index, cell in enumerate(worksheet[1])}
    assert worksheet.cell(row=2, column=headers["Vendor"]).value == "Enriched Vendor"


@pytest.mark.asyncio
async def test_process_document_enrichment_failure_is_non_fatal(monkeypatch):
    async def fail_generate_suggestions_execute(
        *,
        supabase,
        products,
        shop_domain,
        normalization_settings=None,
        trace_event=None,
    ):
        _ = supabase, products, shop_domain, normalization_settings, trace_event
        raise RuntimeError("Synthetic enrichment failure")

    monkeypatch.setattr(
        process_document_uc,
        "generate_suggestions_execute",
        fail_generate_suggestions_execute,
        raising=False,
    )

    supabase = _FakeSupabase(
        model_config={
            "base_url": "http://localhost:11434/v1/",
            "model_id": "deepseek-r1:8b",
            "api_key": "secret",
            "provider": "ollama",
        }
    )
    llm = _FakeLLM(payload={"products": [{"title": "Demo Product"}]})
    tracing = _FakeTracing()

    result = await process_document_uc.execute(
        supabase=supabase,
        llm=llm,
        tracing=tracing,
        ctx=SimpleNamespace(),
        file_bytes=_valid_xlsx_bytes(),
        input_name="catalog.xlsx",
        input_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        run_id="run-import-enrich-fallback",
        shop_domain="store.myshopify.com",
    )

    assert result["result"]["products"][0]["title"] == "Demo Product"
    assert "vendor" not in result["result"]["products"][0]
    assert result["result"]["enrichment_applied"] is False
    assert "Synthetic enrichment failure" in result["result"]["enrichment_warning"]


@pytest.mark.asyncio
async def test_process_document_workbook_ai_enhancements_shows_all_enriched_fields(monkeypatch):
    """The AI Enhancements sheet must list ALL fields changed by enrichment,
    not just metafields.  Non-metafield patches (vendor, seo_title, tags, etc.)
    should appear alongside metafield patches."""

    async def fake_generate_suggestions_execute(
        *,
        supabase,
        products,
        shop_domain,
        normalization_settings=None,
        trace_event=None,
    ):
        _ = supabase, products, shop_domain, normalization_settings, trace_event
        return [
            {
                "product_index": 0,
                "patch_payload": {
                    "vendor": "Enriched Vendor",
                    "seo_title": "Better SEO Title",
                    "tags": "tag-a, tag-b",
                    "metafields": [
                        {
                            "namespace": "extractor",
                            "key": "color_hint",
                            "value": "blue",
                            "type": "single_line_text_field",
                        }
                    ],
                },
                "value_origin": "ai_assisted",
            }
        ]

    monkeypatch.setattr(
        process_document_uc,
        "generate_suggestions_execute",
        fake_generate_suggestions_execute,
        raising=False,
    )

    supabase = _FakeSupabase(
        model_config={
            "base_url": "http://localhost:11434/v1/",
            "model_id": "deepseek-r1:8b",
            "api_key": "secret",
            "provider": "ollama",
        }
    )
    llm = _FakeLLM(
        payload={
            "file_id": "pre-enrichment-file",
            "filename": "catalog-products.xlsx",
            "products": [{"title": "Demo Product"}],
        }
    )
    tracing = _FakeTracing()

    result = await process_document_uc.execute(
        supabase=supabase,
        llm=llm,
        tracing=tracing,
        ctx=SimpleNamespace(),
        file_bytes=_valid_xlsx_bytes(),
        input_name="catalog.xlsx",
        input_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        run_id="run-ai-enhancements-sheet",
        shop_domain="store.myshopify.com",
        write_to_file=True,
    )

    saved = supabase.file.saved_files[0]
    workbook = load_workbook(BytesIO(saved["content"]))
    ai_sheet = workbook["AI Enhancements"]

    # Collect all data rows (skip header)
    rows = []
    for row in ai_sheet.iter_rows(min_row=2, values_only=True):
        attr = row[2]  # Attribute column
        if attr and attr != "No AI-only attributes captured":
            rows.append({"attribute": attr, "value": row[3], "type": row[4]})

    attributes = [r["attribute"] for r in rows]
    # Non-metafield enrichments must appear
    assert "vendor" in attributes, f"vendor missing from AI Enhancements: {attributes}"
    assert "seo_title" in attributes, f"seo_title missing from AI Enhancements: {attributes}"
    assert "tags" in attributes, f"tags missing from AI Enhancements: {attributes}"
    # Metafield enrichments must still appear
    assert "metafield:extractor.color_hint" in attributes, (
        f"metafield missing from AI Enhancements: {attributes}"
    )


@pytest.mark.asyncio
async def test_process_document_passes_file_search_toggle_from_model_config_extra():
    supabase = _FakeSupabase(
        model_config={
            "base_url": "https://api.openai.com/v1",
            "model_id": "gpt-4.1-mini",
            "api_key": "secret",
            "provider": "openai",
            "extra": {"enable_file_search": False},
        }
    )
    llm = _FakeLLM(payload={"products": []})
    tracing = _FakeTracing()

    await process_document_uc.execute(
        supabase=supabase,
        llm=llm,
        tracing=tracing,
        ctx=SimpleNamespace(),
        file_bytes=b"sheet-bytes",
        input_name="catalog.csv",
        input_content_type="text/csv",
        run_id="run-import-file-search-toggle",
        shop_domain="store.myshopify.com",
    )

    assert llm.last_kwargs["model_file_search_enabled"] is False


@pytest.mark.asyncio
async def test_process_document_persists_observability_ids_from_context():
    supabase = _FakeSupabase()
    llm = _FakeLLM(payload={"products": []})
    tracing = _FakeTracing()

    with bind_observability_context(
        request_id="req-process-document-1",
        correlation_id="corr-process-document-1",
    ):
        await process_document_uc.execute(
            supabase=supabase,
            llm=llm,
            tracing=tracing,
            ctx=SimpleNamespace(),
            file_bytes=_valid_xlsx_bytes(),
            input_name="catalog.xlsx",
            input_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            run_id="run-process-observability",
            shop_domain="store.myshopify.com",
        )

    first_run_update = supabase.runs.created_updates[0][1]
    assert first_run_update["request_id"] == "req-process-document-1"
    assert first_run_update["correlation_id"] == "corr-process-document-1"


@pytest.mark.asyncio
async def test_pdf_evidence_feedback_retries_with_complete_payload_and_resolves_index(monkeypatch):
    pdf = _minimal_pdf_bytes()
    _patch_canonical_pdf(monkeypatch, pdf)
    llm = _SequenceLLM([
        {"products": [{"title": "Classic Cotton T-Shirt"}]},
        {"products": [{
            "title": "Classic Cotton T-Shirt",
            "source_refs": [{"value_path": "title", "evidence_index": 1}],
        }]},
    ])
    tracing = _FakeTracing()

    result = await process_document_uc.execute(
        supabase=_FakeSupabase(),
        llm=llm,
        tracing=tracing,
        ctx=SimpleNamespace(services=SimpleNamespace(document_layout=_PdfLayoutService(), collabora=None)),
        file_bytes=pdf,
        input_name="source.pdf",
        input_content_type="application/pdf",
        source_file_id="uploaded-file",
        shop_domain="store.myshopify.com",
        enable_ai_enhancements=False,
    )

    assert len(llm.calls) == 2
    assert "title" in llm.calls[1]["agent_prompt"]
    assert "complete products payload" in llm.calls[1]["agent_prompt"]
    assert llm.calls[0]["extracted_text_override"] == "1. lassic Cotton T-Shirt"
    product = result["result"]["products"][0]
    assert product["value_origins"]["title"] == "source"
    assert product["source_refs"][0]["anchor_id"] == "azure-title"
    assert product["source_refs"][0]["source_file_id"] == "canonical-file"
    assert "evidence_index" not in product["source_refs"][0]
    phases = [event["phase"] for event in tracing.events]
    assert phases.count("pdf_evidence_correction_attempt") == 1
    assert phases.count("pdf_evidence_validation_success") == 1


@pytest.mark.asyncio
async def test_pdf_evidence_gets_exactly_two_corrections_then_fails_without_output(monkeypatch):
    pdf = _minimal_pdf_bytes()
    _patch_canonical_pdf(monkeypatch, pdf)
    llm = _SequenceLLM([
        {"products": [{"title": "Unlinked"}]},
        {"products": [{"title": "Still unlinked"}]},
        {"products": [{"title": "Never linked"}]},
    ])
    supabase = _FakeSupabase()
    tracing = _FakeTracing()

    with pytest.raises(SourceEvidenceValidationFailed, match="SOURCE_EVIDENCE_VALIDATION_FAILED"):
        await process_document_uc.execute(
            supabase=supabase,
            llm=llm,
            tracing=tracing,
            ctx=SimpleNamespace(services=SimpleNamespace(document_layout=_PdfLayoutService(), collabora=None)),
            file_bytes=pdf,
            input_name="source.pdf",
            input_content_type="application/pdf",
            source_file_id="uploaded-file",
            shop_domain="store.myshopify.com",
            write_to_file=True,
            enable_ai_enhancements=False,
        )

    assert len(llm.calls) == 3
    assert all(call["write_to_file"] is False for call in llm.calls)
    assert supabase.file.saved_files == []
    phases = [event["phase"] for event in tracing.events]
    assert phases.count("pdf_evidence_correction_attempt") == 2
    assert phases.count("pdf_evidence_validation_failed") == 1
