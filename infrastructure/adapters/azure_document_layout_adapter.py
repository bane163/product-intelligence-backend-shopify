from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import perf_counter
from typing import Any

import httpx

from application.ports.document_layout_port import DocumentAnchor, DocumentLayout


class AzureDocumentLayoutAdapter:
    """Azure prebuilt-layout adapter. Text is returned to callers but never traced here."""

    def __init__(self, endpoint: str, key: str, *, max_retries: int = 2,
                 trace: Callable[[str, dict[str, Any]], None] | None = None,
                 client: httpx.AsyncClient | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.key = key
        self.max_retries = max(0, max_retries)
        self.trace = trace
        self.client = client

    async def analyze_pdf(self, content: bytes, source_artifact: str) -> DocumentLayout:
        started = perf_counter()
        retries = 0
        owned_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=60)
        try:
            while True:
                try:
                    response = await client.post(
                        f"{self.endpoint}/documentintelligence/documentModels/prebuilt-layout:analyze",
                        params={"api-version": "2024-11-30"},
                        headers={"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/pdf"},
                        content=content,
                    )
                    if response.status_code in {408, 429, 500, 502, 503, 504}:
                        raise httpx.HTTPStatusError("transient Azure response", request=response.request, response=response)
                    response.raise_for_status()
                    payload = response.json()
                    if "operation-location" in response.headers:
                        payload = await self._poll(client, response.headers["operation-location"])
                    layout = _layout_from_payload(payload, source_artifact)
                    self._trace("document_layout_complete", layout, retries, started)
                    return layout
                except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
                    if retries >= self.max_retries:
                        self._trace("document_layout_failed", None, retries, started)
                        raise
                    retries += 1
                    await asyncio.sleep(0.25 * (2 ** (retries - 1)))
        finally:
            if owned_client:
                await client.aclose()

    async def _poll(self, client: httpx.AsyncClient, url: str) -> dict[str, Any]:
        for attempt in range(60):
            response = await client.get(url, headers={"Ocp-Apim-Subscription-Key": self.key})
            response.raise_for_status()
            payload = response.json()
            status = str(payload.get("status", "")).lower()
            if status == "succeeded": return payload
            if status in {"failed", "canceled"}: raise RuntimeError("Azure document layout analysis failed")
            await asyncio.sleep(min(0.25 * (attempt + 1), 2.0))
        raise TimeoutError("Azure document layout analysis timed out")

    def _trace(self, event: str, layout: DocumentLayout | None, retries: int, started: float) -> None:
        if self.trace:
            self.trace(event, {"page_count": layout.page_count if layout else 0,
                               "anchor_count": len(layout.anchors) if layout else 0,
                               "retries": retries, "latency_ms": round((perf_counter() - started) * 1000)})


def _bbox(polygon: list[float], width: float, height: float) -> tuple[float, float, float, float] | None:
    if len(polygon) < 8 or width <= 0 or height <= 0: return None
    xs, ys = polygon[0::2], polygon[1::2]
    values = (min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height)
    return tuple(max(0.0, min(1.0, value)) for value in values)  # type: ignore[return-value]


def _layout_from_payload(payload: dict[str, Any], source_artifact: str) -> DocumentLayout:
    result = payload.get("analyzeResult", payload)
    pages = result.get("pages") if isinstance(result, dict) else []
    anchors: list[DocumentAnchor] = []
    for page_index, page in enumerate(pages or [], start=1):
        page_number = int(page.get("pageNumber") or page_index)
        width, height = float(page.get("width") or 0), float(page.get("height") or 0)
        for role in ("words", "lines"):
            for item_index, item in enumerate(page.get(role) or []):
                box = _bbox(item.get("polygon") or [], width, height)
                text = str(item.get("content") or "").strip()
                if box and text:
                    anchors.append(DocumentAnchor(f"p{page_number}-{role[:-1]}-{item_index}", source_artifact, page_number, box, text, role[:-1]))
    for table_index, table in enumerate(result.get("tables") or []):
        for cell_index, cell in enumerate(table.get("cells") or []):
            regions = cell.get("boundingRegions") or []
            if not regions: continue
            region = regions[0]
            page_number = int(region.get("pageNumber") or 1)
            page = next((p for p in pages or [] if int(p.get("pageNumber") or 0) == page_number), {})
            box = _bbox(region.get("polygon") or [], float(page.get("width") or 0), float(page.get("height") or 0))
            text = str(cell.get("content") or "").strip()
            if box and text:
                anchors.append(DocumentAnchor(f"p{page_number}-table-{table_index}-cell-{cell_index}", source_artifact, page_number, box, text, "table_cell"))
    return DocumentLayout(len(pages or []), tuple(anchors))
