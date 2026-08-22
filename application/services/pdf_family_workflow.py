"""Document workflow adapter for canonical PDF-family extraction.

The processing use case canonicalizes PDF, Office, and image inputs to a PDF and
builds the trusted evidence catalog before invoking this adapter.  This module
restores the proven text-plus-rendered-pages agent workflow without changing the
``LLMPort.extract`` boundary or the spreadsheet Copilot workflow.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import fitz
from pydantic import ValidationError

from ai.models import ProductsList
from application.ports.llm_port import ExtractionRequest
from shared.token_usage import normalize_reported_token_usage


PDF_FAMILY_KINDS = frozenset({"pdf", "docx", "pptx", "image"})
MAX_RENDERED_PAGES = 20

AgentRunner = Callable[
    [ExtractionRequest, str, list[bytes]], Awaitable[Any]
]


def _strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            return stripped[first_newline + 1 : -3].strip()
    return stripped


def render_pdf_pages(content: bytes, *, limit: int = MAX_RENDERED_PAGES) -> list[bytes]:
    """Render bounded visual context from the already-canonical PDF."""
    document = fitz.open(stream=content, filetype="pdf")
    try:
        return [
            document[index]
            .get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            .tobytes("png")
            for index in range(min(document.page_count, limit))
        ]
    finally:
        document.close()


def build_pdf_workflow_prompt(request: ExtractionRequest) -> str:
    """Build the complete correction prompt from server-owned evidence."""
    catalog = request.evidence_catalog
    if catalog is None:
        raise RuntimeError(
            "PDF_WORKFLOW_EVIDENCE_REQUIRED: canonical evidence catalog is missing"
        )
    feedback = (
        "\n\nSERVER VALIDATION FEEDBACK\n"
        f"{request.validation_feedback}\n"
        "Return the complete corrected products payload, not a patch."
        if request.validation_feedback
        else ""
    )
    return (
        f"Extract every Shopify product represented in {request.filename}. Use both the "
        "numbered evidence catalog and rendered pages. Return exactly one ProductsList JSON "
        "object containing the complete products collection, never a partial patch. Each product "
        "owns product-relative source_refs. Never prefix a path with products[index].\n\n"
        "EVIDENCE RULES\n"
        "For every non-empty value copied from the source, include a separate source_ref with its "
        "exact canonical value_path and a numeric evidence_index from the catalog. This includes "
        "core product fields; SEO title and description; image metadata; Google Shopping fields; "
        "booleans; lists such as tags and option values; JSON values; every variant field, including "
        "pricing, SKU, inventory, fulfillment, shipping, tax, weight, and status; and every metafield "
        "value and type. A label or nearby heading is not evidence for a value unless the indexed "
        "catalog entry contains that value. The required product title must always have indexed "
        "source evidence.\n\n"
        "CANONICAL PATH RULES\n"
        "Use paths such as title, descriptionHtml, vendor, productType, status, tags, seo.title, "
        "seo.description, image_src, image_position, image_alt_text, google_shopping_category, "
        "option[color].values, variant[color-red].sku, variant[color-red].price, "
        "variant[color-red].taxable, inventory[color-red].inventory_quantity, "
        "metafield[custom-details].value, and metafield[custom-details].type. In particular, "
        "images[0].src is invalid; use image_src when that represented field exists. Option and "
        "variant subjects must be stable descriptive slugs derived from their names or selected "
        "option values, not array indexes.\n\n"
        "ORIGIN AND PAYLOAD RULES\n"
        "Never return page, bbox, source_file_id, anchor_id, provider, or any other coordinate; "
        "the server resolves those fields. Never guess, derive, default, or invent evidence. For a "
        "non-commerce value that is genuinely derived, defaulted, or AI-assisted, provide an "
        "explicit value_origins entry for the same canonical path instead of fabricated evidence. "
        "Commerce, fulfillment, shipping, tax, pricing, SKU, barcode, weight, and inventory values "
        "must be copied only when indexed evidence supports them. Preserve booleans as booleans and "
        "lists as lists. Leave optional fields blank when the source does not support them. When "
        "server validation feedback is present, correct every cited path and return the complete "
        "ProductsList with every product again, not only the changed fields or products.\n\n"
        "NUMBERED EVIDENCE CATALOG\n"
        f"{catalog.to_prompt_text()}"
        f"{feedback}"
    )


async def _run_structured_agent(
    request: ExtractionRequest,
    prompt: str,
    png_pages: list[bytes],
) -> Any:
    """Run the main-branch structured agent on evidence text and page images."""
    from agent_framework import Content, Message
    from agent_framework.openai import OpenAIChatClient

    if not request.model.api_key.strip():
        raise RuntimeError("PDF_WORKFLOW_AUTH_REQUIRED: active model API key is missing")
    if not request.model.model_id.strip():
        raise RuntimeError("PDF_WORKFLOW_MODEL_REQUIRED: active model id is missing")
    client = OpenAIChatClient(
        model=request.model.model_id,
        api_key=request.model.api_key,
        base_url=request.model.base_url or None,
    )
    agent = client.as_agent(
        name="pdf_catalog_extractor",
        instructions=(
            "You extract Shopify products from a server-numbered evidence catalog and visual "
            "page renderings. Return only the structured ProductsList response. Follow canonical "
            "source-reference paths exactly and never invent coordinates."
        ),
        default_options={
            "response_format": ProductsList,
            **(
                {"max_output_tokens": request.model.max_tokens}
                if request.model.max_tokens is not None
                else {}
            ),
        },
    )
    contents = [Content.from_text(prompt)]
    contents.extend(Content.from_data(page, "image/png") for page in png_pages)
    return await agent.run(Message("user", contents))


class PDFDocumentWorkflowAdapter:
    """Dedicated LLM adapter for canonical PDF, DOCX, PPTX, and image inputs."""

    def __init__(self, *, agent_runner: AgentRunner | None = None) -> None:
        self._agent_runner = agent_runner or _run_structured_agent

    async def extract(self, request: ExtractionRequest) -> ProductsList:
        if request.document_kind not in PDF_FAMILY_KINDS:
            raise RuntimeError(
                f"PDF_WORKFLOW_DOCUMENT_KIND_UNSUPPORTED: {request.document_kind}"
            )
        trace = request.trace_event
        prompt = build_pdf_workflow_prompt(request)
        catalog = request.evidence_catalog
        if catalog is None:  # Kept explicit for type checkers after prompt validation.
            raise RuntimeError(
                "PDF_WORKFLOW_EVIDENCE_REQUIRED: canonical evidence catalog is missing"
            )
        if trace:
            trace(
                phase="pdf_workflow_extract_start",
                message="Starting canonical PDF document workflow",
                payload_preview={
                    "kind": request.document_kind,
                    "evidence_count": len(catalog.entries),
                    "correction": bool(request.validation_feedback),
                },
            )
        pages = render_pdf_pages(request.content)
        if trace:
            trace(
                phase="pdf_workflow_render_done",
                message="Rendered canonical PDF pages for extraction",
                payload_preview={"pages": len(pages)},
            )
        response = await self._agent_runner(request, prompt, pages)
        value = (
            response.get("value")
            if isinstance(response, dict)
            else getattr(response, "value", None)
        )
        text = (
            response.get("text", "")
            if isinstance(response, dict)
            else getattr(response, "text", "")
        ) or ""
        try:
            if isinstance(value, ProductsList):
                result = value
            elif value is not None:
                result = ProductsList.model_validate(value)
            else:
                result = ProductsList.model_validate_json(
                    _strip_markdown_json_fence(text)
                )
        except ValidationError as exc:
            raise RuntimeError(
                "PDF_WORKFLOW_INVALID_PAYLOAD: agent did not return a complete ProductsList"
            ) from exc
        usage = normalize_reported_token_usage(response)
        if trace and usage is not None:
            trace(
                phase="pdf_workflow_usage",
                message="Recorded PDF document workflow usage",
                payload_preview={"usage_reported": True},
                metadata={
                    "usage": usage,
                    "model_name": request.model.model_id,
                    "provider": request.model.provider,
                },
            )
        if trace:
            trace(
                phase="pdf_workflow_extract_done",
                message="Completed canonical PDF document workflow",
                payload_preview={"products": len(result.products)},
            )
        return result
