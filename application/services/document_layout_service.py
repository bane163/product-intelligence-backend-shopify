from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any

from application.ports.document_layout_port import DocumentAnchor, DocumentLayout, DocumentLayoutPort


class PdfLayoutUnavailable(RuntimeError):
    code = "PDF_LAYOUT_UNAVAILABLE"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


def _anchor_id(page: int, index: int, text: str) -> str:
    digest = hashlib.sha1(f"{page}:{index}:{text}".encode()).hexdigest()[:10]
    return f"p{page}-text-{digest}"


def pymupdf_layout(content: bytes, source_file_id: str) -> DocumentLayout:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise PdfLayoutUnavailable("PyMuPDF is not installed") from exc

    document = fitz.open(stream=content, filetype="pdf")
    anchors: list[DocumentAnchor] = []
    blank_pages: list[int] = []
    try:
        for page_index, page in enumerate(document, start=1):
            width, height = float(page.rect.width), float(page.rect.height)
            blocks = page.get_text("blocks", sort=True)
            page_anchors = 0
            for item_index, block in enumerate(blocks):
                text = re.sub(r"\s+", " ", str(block[4] or "")).strip()
                if not text or width <= 0 or height <= 0:
                    continue
                x0, y0, x1, y1 = (float(value) for value in block[:4])
                bbox = tuple(max(0.0, min(1.0, value)) for value in (x0 / width, y0 / height, x1 / width, y1 / height))
                anchors.append(DocumentAnchor(_anchor_id(page_index, item_index, text), source_file_id, page_index, bbox, text, "text_block", "pymupdf"))
                page_anchors += 1
            if page_anchors == 0 and page.get_images(full=True):
                blank_pages.append(page_index)
        expected = tuple(range(1, document.page_count + 1))
    finally:
        document.close()
    if blank_pages:
        raise PdfLayoutUnavailable(
            "embedded text is missing on page(s) " + ", ".join(map(str, blank_pages)) + "; configure Azure Document Intelligence for scanned PDFs"
        )
    return DocumentLayout(len(expected), tuple(anchors), "pymupdf", expected, expected)


class DocumentLayoutService:
    def __init__(self, azure: DocumentLayoutPort | None, *, trace: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.azure = azure
        self.trace = trace

    async def analyze_pdf(self, content: bytes, source_file_id: str) -> DocumentLayout:
        digital: DocumentLayout | None = None
        azure_layout: DocumentLayout | None = None
        if self.azure is not None:
            self._trace("pdf_layout_azure_start", {})
            try:
                azure_layout = await self.azure.analyze_pdf(content, source_file_id)
                self._trace("pdf_layout_azure_success", {"covered_pages": list(azure_layout.covered_pages), "anchors": len(azure_layout.anchors)})
            except Exception as exc:
                self._trace("pdf_layout_azure_failure", {"error_type": type(exc).__name__})

        try:
            import fitz
            probe = fitz.open(stream=content, filetype="pdf")
            try:
                actual_expected = tuple(range(1, probe.page_count + 1))
            finally:
                probe.close()
        except Exception:
            actual_expected = azure_layout.expected_pages if azure_layout else ()
        if azure_layout is not None and azure_layout.expected_pages != actual_expected:
            azure_layout = DocumentLayout(
                len(actual_expected), azure_layout.anchors, azure_layout.provider,
                actual_expected, azure_layout.covered_pages,
            )

        if azure_layout is None or set(azure_layout.covered_pages) != set(azure_layout.expected_pages):
            try:
                digital = pymupdf_layout(content, source_file_id)
                self._trace("pdf_layout_fallback_used", {"pages": digital.page_count})
            except PdfLayoutUnavailable:
                if azure_layout is None:
                    raise

        if azure_layout is None:
            assert digital is not None
            return digital
        missing = set(azure_layout.expected_pages) - set(azure_layout.covered_pages)
        if not missing:
            return azure_layout
        if digital is None:
            raise PdfLayoutUnavailable("Azure returned incomplete page coverage and missing pages have no embedded text")
        additions = tuple(anchor for anchor in digital.anchors if anchor.page in missing)
        covered = tuple(sorted(set(azure_layout.covered_pages) | (missing & set(digital.covered_pages))))
        if set(covered) != set(azure_layout.expected_pages):
            raise PdfLayoutUnavailable("layout providers could not cover every PDF page")
        return DocumentLayout(azure_layout.page_count, azure_layout.anchors + additions, "hybrid", azure_layout.expected_pages, covered)

    def _trace(self, event: str, payload: dict[str, Any]) -> None:
        if self.trace:
            self.trace(event, payload)
