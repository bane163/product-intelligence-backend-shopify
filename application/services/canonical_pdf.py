from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
import os

from PIL import Image, ImageOps

from application.services.document_formats import DocumentFormat

CANONICALIZATION_CONTRACT_VERSION = "canonical-pdf-v1"
CANONICAL_FILE_ORIGIN = "canonical_pdf"


class DocumentPdfConversionUnavailable(RuntimeError):
    code = "DOCUMENT_PDF_CONVERSION_UNAVAILABLE"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class DocumentTextEvidenceUnavailable(RuntimeError):
    code = "DOCUMENT_TEXT_EVIDENCE_UNAVAILABLE"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


@dataclass(frozen=True)
class CanonicalPdf:
    content: bytes
    file_id: str
    filename: str
    content_type: str = "application/pdf"
    cache_hit: bool = False


def canonical_file_id(source_file_id: str, content: bytes) -> str:
    source_sha = hashlib.sha256(content).hexdigest()
    digest = hashlib.sha256(
        f"{source_file_id}:{source_sha}:{CANONICALIZATION_CONTRACT_VERSION}".encode()
    ).hexdigest()
    return f"canonical/{digest}.pdf"


def validate_pdf(content: bytes) -> int:
    if not content.startswith(b"%PDF"):
        raise DocumentPdfConversionUnavailable("converter did not return a PDF")
    try:
        import fitz
        document = fitz.open(stream=content, filetype="pdf")
        try:
            if document.page_count < 1:
                raise ValueError("PDF contains no pages")
            return document.page_count
        finally:
            document.close()
    except DocumentPdfConversionUnavailable:
        raise
    except Exception as exc:
        raise DocumentPdfConversionUnavailable("converted PDF is invalid") from exc


def image_to_pdf(content: bytes) -> bytes:
    try:
        with Image.open(BytesIO(content)) as source:
            if getattr(source, "n_frames", 1) != 1 or bool(getattr(source, "is_animated", False)):
                raise ValueError("animated or multi-frame images are not supported")
            source.load()
            displayed = ImageOps.exif_transpose(source)
            if displayed.mode in {"RGBA", "LA"} or "transparency" in displayed.info:
                rgba = displayed.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                displayed = flattened
            else:
                displayed = displayed.convert("RGB")
            output = BytesIO()
            dpi = displayed.info.get("dpi", source.info.get("dpi", (72, 72)))
            resolution = float(dpi[0]) if isinstance(dpi, tuple) and len(dpi) == 2 and min(dpi) > 0 else 72.0
            displayed.save(output, format="PDF", resolution=resolution)
            pdf = output.getvalue()
    except Exception as exc:
        raise ValueError(f"Invalid standalone image: {exc}") from exc
    validate_pdf(pdf)
    return pdf


async def ensure_canonical_pdf(
    *, supabase, collabora, source_file_id: str, source_content: bytes,
    source_name: str, source_content_type: str, document_format: DocumentFormat,
    shop_domain: str, collabora_base_url: str | None = None,
) -> CanonicalPdf:
    if document_format.kind == "pdf":
        validate_pdf(source_content)
        return CanonicalPdf(source_content, source_file_id, source_name)
    if document_format.kind not in {"docx", "pptx", "image"}:
        raise ValueError("Only Office documents and standalone images are canonicalized")
    artifact_id = canonical_file_id(source_file_id, source_content)
    existing = supabase.file.get_file(artifact_id)
    if existing and str(existing.get("shop_domain") or "").casefold() != shop_domain.casefold():
        raise PermissionError("Canonical artifact belongs to a different tenant")
    if existing:
        cached = existing.get("content")
        if isinstance(cached, (bytes, bytearray)):
            try:
                validate_pdf(bytes(cached))
            except DocumentPdfConversionUnavailable:
                pass
            else:
                return CanonicalPdf(bytes(cached), artifact_id, str(existing.get("name") or "source.pdf"), cache_hit=True)
    if document_format.kind == "image":
        pdf = image_to_pdf(source_content)
    else:
        try:
            pdf = await collabora.convert_document_to_pdf_collabora(
                source_content, filename=source_name,
                content_type=source_content_type or "application/octet-stream",
                collabora_base_url=collabora_base_url or os.getenv("COLLABORA_URL", "http://localhost:9980"),
            )
        except Exception as exc:
            raise DocumentPdfConversionUnavailable("Office-to-PDF conversion failed") from exc
        validate_pdf(pdf)
    base = os.path.splitext(os.path.basename(source_name))[0] or "source"
    filename = f"{base}.canonical.pdf"
    supabase.file.save_file(artifact_id, name=filename, content=pdf,
                            content_type="application/pdf", file_origin=CANONICAL_FILE_ORIGIN,
                            shop_domain=shop_domain)
    return CanonicalPdf(pdf, artifact_id, filename)
