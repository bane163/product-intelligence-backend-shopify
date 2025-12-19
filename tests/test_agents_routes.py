import io
import os
import pytest
from httpx import AsyncClient, ASGITransport

from main import app


@pytest.mark.asyncio
async def test_upload_and_wopi_get_file_contents():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        file_bytes = b"hello world"
        files = {"file": ("test.txt", io.BytesIO(file_bytes), "text/plain")}
        r = await ac.post("/agents/upload", files=files)
        assert r.status_code == 200
        body = r.json()
        assert "file_id" in body
        file_id = body["file_id"]

        # GET contents via WOPI
        r2 = await ac.get(f"/agents/wopi/files/{file_id}/contents")
        assert r2.status_code == 200
        assert r2.content == file_bytes


@pytest.mark.asyncio
async def test_file_info_and_delete():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        file_bytes = b"abc123"
        files = {
            "file": (
                "doc.xlsx",
                io.BytesIO(file_bytes),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        r = await ac.post("/agents/upload", files=files)
        assert r.status_code == 200
        file_id = r.json()["file_id"]

        r_info = await ac.get(f"/agents/files/{file_id}")
        assert r_info.status_code == 200
        info = r_info.json()
        assert info["filename"] == "doc.xlsx"

        r_del = await ac.delete(f"/agents/files/{file_id}")
        assert r_del.status_code == 200
        assert r_del.json()["status"] == "deleted"

        # now should 404
        r_info2 = await ac.get(f"/agents/files/{file_id}")
        assert r_info2.status_code == 404


@pytest.mark.asyncio
async def test_preview_generates_png(monkeypatch):
    # Patch conversion helpers to return canned outputs
    async def fake_convert_excel_to_pdf_collabora(content, collabora_base_url=None):
        return b"PDF_BYTES"

    async def fake_convert_pdf_to_png_collabora(pdf_bytes, collabora_base_url=None):
        return [b"PNG_BYTES_PAGE_1"]

    monkeypatch.setenv("COLLABORA_URL", "http://localhost:9980")

    import ai.collabora_utils as cu

    monkeypatch.setattr(
        cu, "convert_excel_to_pdf_collabora", fake_convert_excel_to_pdf_collabora
    )
    monkeypatch.setattr(
        cu, "convert_pdf_to_png_collabora", fake_convert_pdf_to_png_collabora
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        file_bytes = b"sheetdata"
        files = {
            "file": (
                "sheet.xlsx",
                io.BytesIO(file_bytes),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        r = await ac.post("/agents/upload", files=files)
        file_id = r.json()["file_id"]

        r_preview = await ac.get(f"/agents/preview/{file_id}")
        assert r_preview.status_code == 200
        assert r_preview.content == b"PNG_BYTES_PAGE_1"
