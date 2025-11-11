import io
import zipfile
from typing import List

import httpx


async def convert_excel_to_pdf_collabora(
    file_bytes: bytes,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> bytes:
    """Post an Excel file to Collabora's convert-to/pdf endpoint and return PDF bytes."""
    convert_url = collabora_base_url.rstrip("/") + "/lool/convert-to/pdf"

    files = {
        "file": (
            "file.xlsx",
            file_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(convert_url, files=files)
        resp.raise_for_status()
        return resp.content


async def convert_pdf_to_png_collabora(
    pdf_bytes: bytes,
    collabora_base_url: str = "http://localhost:8080",
    timeout: int = 60,
) -> List[bytes]:
    """Convert a PDF to PNG(s) using Collabora; return list of PNG bytes.

    Collabora may return a single PNG (image/png) or a zip archive of PNGs
    (application/zip) for multi-page documents. Detect both forms.
    """
    convert_url = collabora_base_url.rstrip("/") + "/lool/convert-to/png"

    files = {"file": ("file.pdf", pdf_bytes, "application/pdf")}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(convert_url, files=files)
        resp.raise_for_status()
        content = resp.content

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
