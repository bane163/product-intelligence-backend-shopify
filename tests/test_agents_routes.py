import io
import json
import os
import pytest
from httpx import AsyncClient, ASGITransport

from app_context import get_app_context
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
async def test_upload_csv_converts_to_xlsx(monkeypatch):
    async def fake_convert_csv_to_excel(content, collabora_base_url=None, timeout=60):
        _ = (content, collabora_base_url, timeout)
        return b"XLSX_BYTES"

    import ai.collabora_utils as cu

    monkeypatch.setattr(cu, "convert_csv_to_excel", fake_convert_csv_to_excel)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        files = {"file": ("products.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}
        r = await ac.post("/agents/upload", files=files)
        assert r.status_code == 200
        file_id = r.json()["file_id"]

        r_info = await ac.get(f"/agents/files/{file_id}")
        assert r_info.status_code == 200
        info = r_info.json()
        assert info["filename"] == "products.xlsx"
        assert info["content_type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert info["size"] == len(b"XLSX_BYTES")


@pytest.mark.asyncio
async def test_upload_csv_conversion_failure_returns_500_and_skips_save(monkeypatch):
    async def fake_convert_csv_to_excel(content, collabora_base_url=None, timeout=60):
        _ = (content, collabora_base_url, timeout)
        raise RuntimeError("conversion failed")

    import ai.collabora_utils as cu

    monkeypatch.setattr(cu, "convert_csv_to_excel", fake_convert_csv_to_excel)

    ctx = get_app_context()
    save_called = {"value": False}

    def fail_if_save_called(*args, **kwargs):
        _ = (args, kwargs)
        save_called["value"] = True
        raise AssertionError("save_file should not be called when CSV conversion fails")

    monkeypatch.setattr(ctx.services.supabase, "save_file", fail_if_save_called)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        files = {"file": ("products.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")}
        r = await ac.post("/agents/upload", files=files)
        assert r.status_code == 500
        assert "CSV to Excel conversion failed" in r.json()["detail"]
        assert save_called["value"] is False


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


@pytest.mark.asyncio
async def test_save_product_draft():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        payload = {
            "products_json": json.dumps([{"title": "Draft Product"}]),
            "run_id": "run-1",
            "import_mode": "create",
            "draft_name": "products.xlsx",
        }
        r = await ac.post("/agents/product-drafts", data=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["product_count"] == 1
        assert body["import_mode"] == "create"
        assert body["draft_name"] == "products.xlsx"
        assert "draft_id" in body


@pytest.mark.asyncio
async def test_submit_products_blocks_dry_run():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        payload = {
            "products_json": json.dumps([{"title": "Demo"}]),
            "import_mode": "dry_run",
        }
        r = await ac.post("/agents/submit-products", data=payload)
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_list_and_get_product_draft():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        payload = {
            "products_json": json.dumps([{"title": "Draft A"}]),
            "run_id": "run-a",
            "import_mode": "create",
        }
        created = await ac.post("/agents/product-drafts", data=payload)
        assert created.status_code == 200
        draft_id = created.json()["draft_id"]

        listed = await ac.get("/agents/product-drafts")
        assert listed.status_code == 200
        drafts = listed.json()["drafts"]
        assert any(d.get("draft_id") == draft_id for d in drafts)

        detail = await ac.get(f"/agents/product-drafts/{draft_id}")
        assert detail.status_code == 200
        assert detail.json()["draft"]["draft_id"] == draft_id

        resume = await ac.post(f"/agents/product-drafts/{draft_id}/resume-file")
        assert resume.status_code == 200
        resume_body = resume.json()
        assert "file_id" in resume_body
        assert resume_body["filename"].endswith(".xlsx")
