import io
import zipfile
from typing import List

import httpx


def _convert_to_urls(collabora_base_url: str, target: str) -> list[str]:
    base = collabora_base_url.rstrip("/")
    return [f"{base}/cool/convert-to/{target}", f"{base}/lool/convert-to/{target}"]


def _extract_link_targets_urls(collabora_base_url: str) -> list[str]:
    base = collabora_base_url.rstrip("/")
    return [
        f"{base}/cool/extract-link-targets",
        f"{base}/lool/extract-link-targets",
    ]


async def _post_convert_to(
    *,
    collabora_base_url: str,
    target: str,
    filename: str,
    file_bytes: bytes,
    content_type: str,
    timeout: int,
) -> bytes:
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for convert_url in _convert_to_urls(collabora_base_url, target):
            try:
                resp = await client.post(
                    convert_url,
                    files={"file": (filename, file_bytes, content_type)},
                )
                resp.raise_for_status()
                return resp.content
            except (
                Exception
            ) as exc:  # pragma: no cover - exercised by fallback behavior
                last_exc = exc
    if last_exc:
        raise last_exc
    raise RuntimeError("Collabora convert-to request failed")


async def extract_link_targets_collabora(
    file_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> dict[str, dict[str, str]]:
    """Extract named link targets from a document via Collabora linking API."""
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for endpoint in _extract_link_targets_urls(collabora_base_url):
            try:
                resp = await client.post(
                    endpoint,
                    files={
                        "data": (
                            filename or "document.bin",
                            file_bytes,
                            content_type or "application/octet-stream",
                        )
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    return {}
                raw_targets = payload.get("Targets")
                if not isinstance(raw_targets, dict):
                    return {}
                normalized: dict[str, dict[str, str]] = {}
                for group_name, entries in raw_targets.items():
                    if not isinstance(group_name, str) or not isinstance(entries, dict):
                        continue
                    normalized_entries: dict[str, str] = {}
                    for label, target in entries.items():
                        if not isinstance(label, str) or not isinstance(target, str):
                            continue
                        cleaned_target = target.strip()
                        if not cleaned_target:
                            continue
                        normalized_entries[label] = cleaned_target
                    if normalized_entries:
                        normalized[group_name] = normalized_entries
                return normalized
            except Exception as exc:  # pragma: no cover - exercised via fallback behavior
                last_exc = exc
    if last_exc:
        raise last_exc
    return {}


async def convert_excel_to_pdf_collabora(
    file_bytes: bytes,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> bytes:
    """Post a document (e.g., spreadsheet) to Collabora's convert-to/pdf endpoint and return PDF bytes."""
    return await _post_convert_to(
        collabora_base_url=collabora_base_url,
        target="pdf",
        filename="file.xlsx",
        file_bytes=file_bytes,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        timeout=timeout,
    )


async def convert_document_to_pdf_collabora(
    file_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> bytes:
    """Post a document to Collabora's convert-to/pdf endpoint and return PDF bytes."""
    return await _post_convert_to(
        collabora_base_url=collabora_base_url,
        target="pdf",
        filename=filename or "document.bin",
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream",
        timeout=timeout,
    )


async def convert_csv_to_excel(
    file_bytes: bytes,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> bytes:
    """Post a CSV file to Collabora's convert-to/xlsx endpoint and return XLSX bytes."""
    return await convert_document_to_xlsx_collabora(
        file_bytes,
        filename="file.csv",
        content_type="text/csv",
        collabora_base_url=collabora_base_url,
        timeout=timeout,
    )


async def convert_document_to_xlsx_collabora(
    file_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> bytes:
    """Post a document to Collabora's convert-to/xlsx endpoint and return XLSX bytes."""
    return await _post_convert_to(
        collabora_base_url=collabora_base_url,
        target="xlsx",
        filename=filename or "document.bin",
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream",
        timeout=timeout,
    )


async def convert_pdf_to_png_collabora(
    pdf_bytes: bytes,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> List[bytes]:
    """Convert a PDF to PNG(s) using Collabora; return list of PNG bytes.

    Collabora may return a single PNG (image/png) or a zip archive of PNGs
    (application/zip) for multi-page documents. Detect both forms.
    """
    content = await _post_convert_to(
        collabora_base_url=collabora_base_url,
        target="png",
        filename="file.pdf",
        file_bytes=pdf_bytes,
        content_type="application/pdf",
        timeout=timeout,
    )

    # If it's a zip, extract png files
    try:
        b = io.BytesIO(content)
        if zipfile.is_zipfile(b):
            pngs: List[bytes] = []
            with zipfile.ZipFile(b) as z:
                for name in z.namelist():
                    if name.lower().endswith(".png"):
                        pngs.append(z.read(name))
            return pngs
    except Exception:
        # fall through and treat as single image
        pass

    # Not a zip — assume the response body is a single PNG
    return [content]


async def convert_document_to_png_collabora(
    file_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> list[bytes]:
    """Convert an arbitrary supported document to PNG(s), preferring direct conversion."""
    lower_name = filename.lower()
    normalized_content_type = (
        (content_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    )
    spreadsheet_exts = (".xlsx", ".xlsm", ".xls", ".ods", ".csv")
    spreadsheet_types = {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
        "application/vnd.ms-excel",
        "application/vnd.oasis.opendocument.spreadsheet",
        "text/csv",
    }
    if lower_name.endswith(".pdf") or normalized_content_type == "application/pdf":
        return await convert_pdf_to_png_collabora(
            file_bytes,
            collabora_base_url=collabora_base_url,
            timeout=timeout,
        )
    if (
        lower_name.endswith(spreadsheet_exts)
        or normalized_content_type in spreadsheet_types
    ):
        pdf_bytes = await _post_convert_to(
            collabora_base_url=collabora_base_url,
            target="pdf",
            filename=filename or "document.bin",
            file_bytes=file_bytes,
            content_type=content_type or "application/octet-stream",
            timeout=timeout,
        )
        return await convert_pdf_to_png_collabora(
            pdf_bytes,
            collabora_base_url=collabora_base_url,
        )

    direct_png = await _post_convert_to(
        collabora_base_url=collabora_base_url,
        target="png",
        filename=filename or "document.bin",
        file_bytes=file_bytes,
        content_type=content_type or "application/octet-stream",
        timeout=timeout,
    )
    try:
        b = io.BytesIO(direct_png)
        if zipfile.is_zipfile(b):
            pngs: List[bytes] = []
            with zipfile.ZipFile(b) as z:
                for name in z.namelist():
                    if name.lower().endswith(".png"):
                        pngs.append(z.read(name))
            if pngs:
                return pngs
    except Exception:
        pass
    return [direct_png]
