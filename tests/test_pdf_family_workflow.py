from types import SimpleNamespace

import fitz
import pytest

from ai.models import ProductsList
from application.ports.llm_port import ExtractionRequest, ModelConfiguration
from application.services.pdf_evidence_index import EvidenceCatalog, EvidenceEntry
from application.services.pdf_family_workflow import (
    PDFDocumentWorkflowAdapter,
    _run_structured_agent,
    build_pdf_workflow_prompt,
    render_pdf_pages,
)


def _pdf() -> bytes:
    document = fitz.open()
    document.new_page().insert_text((72, 72), "Classic Planter SKU CP-1")
    content = document.tobytes()
    document.close()
    return content


def _request(**overrides) -> ExtractionRequest:
    values = {
        "document_kind": "pdf",
        "content": _pdf(),
        "filename": "catalog.pdf",
        "content_type": "application/pdf",
        "model": ModelConfiguration(
            provider="openai",
            base_url="https://api.openai.com/v1",
            model_id="gpt-test",
            api_key="secret",
        ),
        "evidence_catalog": EvidenceCatalog(
            (
                EvidenceEntry(
                    1,
                    "Classic Planter",
                    1,
                    (0.1, 0.1, 0.4, 0.2),
                    "title-anchor",
                    "azure",
                ),
            )
        ),
    }
    values.update(overrides)
    return ExtractionRequest(**values)


def test_prompt_uses_numbered_catalog_and_canonical_correction_rules():
    prompt = build_pdf_workflow_prompt(
        _request(
            validation_feedback=(
                "images[0].src is not a represented canonical path; title lacks evidence"
            )
        )
    )

    assert "1. Classic Planter" in prompt
    assert "images[0].src is invalid; use image_src" in prompt
    assert "Return the complete corrected products payload" in prompt
    assert "never return page, bbox" in prompt.casefold()


def test_prompt_preserves_main_branch_detailed_extraction_rules():
    prompt = build_pdf_workflow_prompt(_request()).casefold()

    for rule in (
        "seo title and description",
        "google shopping fields",
        "booleans",
        "lists such as tags and option values",
        "json values",
        "every variant field",
        "inventory",
        "metafield value and type",
        "seo.title",
        "option[color].values",
        "variant[color-red].taxable",
        "complete productslist with every product again",
    ):
        assert rule in prompt


def test_canonical_pdf_rendering_produces_visual_pages():
    pages = render_pdf_pages(_pdf())

    assert len(pages) == 1
    assert pages[0].startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_adapter_collects_structured_products_and_forwards_feedback():
    captured = {}

    async def runner(request, prompt, pages):
        captured.update(request=request, prompt=prompt, pages=pages)
        return SimpleNamespace(
            value=ProductsList(
                products=[
                    {
                        "title": "Classic Planter",
                        "source_refs": [
                            {"value_path": "title", "evidence_index": 1}
                        ],
                    }
                ]
            ),
            text="",
        )

    adapter = PDFDocumentWorkflowAdapter(agent_runner=runner)
    result = await adapter.extract(
        _request(validation_feedback="Correct and return the complete payload")
    )

    assert result.products[0].title == "Classic Planter"
    assert captured["request"].document_kind == "pdf"
    assert len(captured["pages"]) == 1
    assert "Correct and return the complete payload" in captured["prompt"]


@pytest.mark.asyncio
async def test_adapter_requires_server_numbered_evidence():
    adapter = PDFDocumentWorkflowAdapter(agent_runner=lambda *_: None)

    with pytest.raises(RuntimeError, match="EVIDENCE_REQUIRED"):
        await adapter.extract(_request(evidence_catalog=None))


@pytest.mark.asyncio
async def test_structured_runner_uses_current_agent_framework_multimodal_api(monkeypatch):
    from agent_framework.openai import OpenAIChatClient

    captured = {}

    class Agent:
        async def run(self, message):
            captured["message"] = message
            return SimpleNamespace(value=ProductsList(products=[{"title": "Demo"}]))

    def as_agent(client, **options):
        captured["client"] = client
        captured["options"] = options
        return Agent()

    monkeypatch.setattr(OpenAIChatClient, "as_agent", as_agent)
    response = await _run_structured_agent(_request(), "Evidence", [b"png-bytes"])

    assert response.value.products[0].title == "Demo"
    assert captured["client"].model == "gpt-test"
    assert captured["options"]["default_options"]["response_format"] is ProductsList
    assert [content.type for content in captured["message"].contents] == ["text", "data"]
