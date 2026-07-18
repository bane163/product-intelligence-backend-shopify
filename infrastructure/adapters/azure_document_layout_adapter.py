from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import perf_counter
from typing import Any

from application.ports.document_layout_port import DocumentAnchor, DocumentLayout


class AzureDocumentLayoutAdapter:
    """Official async SDK adapter for the 2024-11-30 GA layout API."""

    def __init__(self, endpoint: str, key: str, *, max_retries: int = 2,
                 trace: Callable[[str, dict[str, Any]], None] | None = None,
                 client: Any | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.key = key
        self.max_retries = max(0, max_retries)
        self.trace = trace
        self.client = client

    async def analyze_pdf(self, content: bytes, source_artifact: str) -> DocumentLayout:
        started = perf_counter()
        client = self.client
        owned = client is None
        if client is None:
            try:
                from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
                from azure.core.credentials import AzureKeyCredential
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("azure-ai-documentintelligence==1.0.2 is required") from exc
            client = DocumentIntelligenceClient(
                endpoint=self.endpoint,
                credential=AzureKeyCredential(self.key),
                api_version="2024-11-30",
            )
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    poller = await client.begin_analyze_document(
                        "prebuilt-layout", body=content, content_type="application/pdf"
                    )
                    result = await poller.result()
                    layout = _layout_from_result(result, source_artifact)
                    self._trace("document_layout_complete", layout, attempt, started)
                    return layout
                except Exception:
                    if attempt >= self.max_retries:
                        self._trace("document_layout_failed", None, attempt, started)
                        raise
                    await asyncio.sleep(0.25 * (2 ** attempt))
        finally:
            if owned:
                await client.close()

    def _trace(self, event: str, layout: DocumentLayout | None, retries: int, started: float) -> None:
        if self.trace:
            self.trace(event, {"page_count": layout.page_count if layout else 0,
                               "anchor_count": len(layout.anchors) if layout else 0,
                               "retries": retries, "latency_ms": round((perf_counter() - started) * 1000)})


def _value(obj: Any, snake: str, camel: str | None = None, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(snake, obj.get(camel or snake, default))
    return getattr(obj, snake, getattr(obj, camel or snake, default))


def _bbox(polygon: Any, width: float, height: float) -> tuple[float, float, float, float] | None:
    if not polygon or width <= 0 or height <= 0:
        return None
    points: list[tuple[float, float]] = []
    if isinstance(polygon[0], (int, float)):
        points = list(zip(polygon[0::2], polygon[1::2]))
    else:
        points = [(float(_value(point, "x", default=0)), float(_value(point, "y", default=0))) for point in polygon]
    if len(points) < 4:
        return None
    xs, ys = zip(*points)
    values = (min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height)
    return tuple(max(0.0, min(1.0, value)) for value in values)  # type: ignore[return-value]


def _layout_from_result(result: Any, source_artifact: str) -> DocumentLayout:
    result = _value(result, "analyze_result", "analyzeResult", result)
    pages = _value(result, "pages", default=[]) or []
    anchors: list[DocumentAnchor] = []
    covered: set[int] = set()
    for page_index, page in enumerate(pages, start=1):
        page_number = int(_value(page, "page_number", "pageNumber", page_index))
        covered.add(page_number)
        width, height = float(_value(page, "width", default=0) or 0), float(_value(page, "height", default=0) or 0)
        for role in ("words", "lines"):
            for item_index, item in enumerate(_value(page, role, default=[]) or []):
                box = _bbox(_value(item, "polygon", default=[]), width, height)
                text = str(_value(item, "content", default="") or "").strip()
                if box and text:
                    anchors.append(DocumentAnchor(f"p{page_number}-{role[:-1]}-{item_index}", source_artifact, page_number, box, text, role[:-1], "azure"))
                    covered.add(page_number)
    for table_index, table in enumerate(_value(result, "tables", default=[]) or []):
        for cell_index, cell in enumerate(_value(table, "cells", default=[]) or []):
            regions = _value(cell, "bounding_regions", "boundingRegions", []) or []
            if not regions:
                continue
            region = regions[0]
            page_number = int(_value(region, "page_number", "pageNumber", 1))
            page = next((item for index, item in enumerate(pages, 1) if int(_value(item, "page_number", "pageNumber", index)) == page_number), None)
            if page is None:
                continue
            box = _bbox(_value(region, "polygon", default=[]), float(_value(page, "width", default=0) or 0), float(_value(page, "height", default=0) or 0))
            text = str(_value(cell, "content", default="") or "").strip()
            if box and text:
                anchors.append(DocumentAnchor(f"p{page_number}-table-{table_index}-cell-{cell_index}", source_artifact, page_number, box, text, "table_cell", "azure"))
                covered.add(page_number)
    page_count = max((int(_value(page, "page_number", "pageNumber", index)) for index, page in enumerate(pages, 1)), default=0)
    expected = tuple(range(1, page_count + 1))
    return DocumentLayout(page_count, tuple(anchors), "azure", expected, tuple(sorted(covered)))


# Kept for existing contract tests that feed raw REST-shaped dictionaries.
def _layout_from_payload(payload: dict[str, Any], source_artifact: str) -> DocumentLayout:
    return _layout_from_result(payload.get("analyzeResult", payload), source_artifact)
