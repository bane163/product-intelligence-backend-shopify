import io
import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, ASGITransport
from PIL import Image

from app_context import get_app_context
from api.agents.files_helper import _generate_thumbnail_bytes, _is_blank_png
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
async def test_bulk_delete_files():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        ids: list[str] = []
        for name in ("doc-a.xlsx", "doc-b.xlsx"):
            files = {
                "file": (
                    name,
                    io.BytesIO(b"bulk-delete"),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            }
            uploaded = await ac.post("/agents/upload", files=files)
            assert uploaded.status_code == 200
            ids.append(uploaded.json()["file_id"])

        missing_id = "missing-file-id"
        bulk_response = await ac.post(
            "/agents/files/bulk-delete", json={"ids": [ids[0], missing_id, ids[1]]}
        )
        assert bulk_response.status_code == 200
        body = bulk_response.json()
        assert body["deleted_ids"] == [ids[0], missing_id, ids[1]]
        assert body["failed_ids"] == []

        assert (await ac.get(f"/agents/files/{ids[0]}")).status_code == 404
        assert (await ac.get(f"/agents/files/{ids[1]}")).status_code == 404


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
        assert (
            info["content_type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert info["size"] == len(b"XLSX_BYTES")


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_file_type():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        files = {
            "file": ("unsupported.zip", io.BytesIO(b"zip-data"), "application/zip")
        }
        response = await ac.post("/agents/upload", files=files)
        assert response.status_code == 415
        assert "Unsupported file type" in response.json()["detail"]


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
        assert "CSV conversion failed" in r.json()["detail"]
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
            "input_file_id": "input-file-1",
            "input_filename": "source.xlsx",
        }
        r = await ac.post("/agents/product-drafts", data=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["product_count"] == 1
        assert body["import_mode"] == "create"
        assert body["draft_name"] == "products.xlsx"
        assert body["input_file_id"] == "input-file-1"
        assert body["input_filename"] == "source.xlsx"
        assert "draft_id" in body


@pytest.mark.asyncio
async def test_submit_products_auto_mode(monkeypatch):
    async def fake_create_product_from_input(self, product):
        return {
            "data": {
                "productCreate": {
                    "product": {
                        "id": "gid://shopify/Product/2",
                        "title": product.get("title"),
                    },
                    "userErrors": [],
                }
            }
        }

    import api.agents.submit as submit_api

    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "create_product_from_input",
        fake_create_product_from_input,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        payload = {
            "products_json": json.dumps([{"title": "Demo"}]),
            "import_mode": "auto",
        }
        r = await ac.post("/agents/submit-products", data=payload)
        assert r.status_code == 200
        assert r.json()["success_count"] == 1


@pytest.mark.asyncio
async def test_submit_products_auto_updates_when_id_present(monkeypatch):
    async def fake_update_product_from_input(self, product):
        return {
            "data": {
                "productUpdate": {
                    "product": {"id": product.get("id"), "title": product.get("title")},
                    "userErrors": [],
                }
            }
        }

    async def fail_create_product_from_input(self, product):
        _ = (self, product)
        raise AssertionError(
            "create_product_from_input should not be called when id is present"
        )

    import api.agents.submit as submit_api

    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "update_product_from_input",
        fake_update_product_from_input,
    )
    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "create_product_from_input",
        fail_create_product_from_input,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        payload = {
            "products_json": json.dumps(
                [{"id": "gid://shopify/Product/99", "title": "Existing Demo"}]
            ),
            "import_mode": "auto",
        }
        r = await ac.post("/agents/submit-products", data=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["success_count"] == 1
        assert body["results"][0]["mode"] == "update"


@pytest.mark.asyncio
async def test_submit_products_ai_enhancements_applies_audit_suggestions(monkeypatch):
    created_payloads: list[dict[str, Any]] = []
    option_payloads: list[dict[str, Any]] = []
    variant_payloads: list[dict[str, Any]] = []

    async def fake_generate_suggestions_execute(
        *,
        supabase,
        products,
        shop_domain,
        normalization_settings=None,
        trace_event=None,
    ):
        _ = (supabase, products, shop_domain, normalization_settings, trace_event)
        return [
            {
                "product_index": 0,
                "patch_payload": {
                    "vendor": "Enriched Vendor",
                    "tags": "alpha, beta",
                    "seo_title": "SEO Demo",
                    "metafields": [
                        {
                            "namespace": "extractor",
                            "key": "source_confidence",
                            "type": "single_line_text_field",
                            "value": "high",
                        }
                    ],
                    "variant_operations": {
                        "create_options": [{"name": "Size", "values": ["S", "M"]}],
                        "create_variants": [
                            {
                                "option_values": [{"option_name": "Size", "name": "S"}],
                                "sku": "DEMO-S",
                            }
                        ],
                    },
                },
            }
        ]

    async def fake_create_product_from_input(self, product):
        created_payloads.append(dict(product))
        return {
            "data": {
                "productCreate": {
                    "product": {
                        "id": "gid://shopify/Product/555",
                        "title": product.get("title"),
                    },
                    "userErrors": [],
                }
            }
        }

    async def fake_create_product_options(self, product_id, options):
        option_payloads.append({"product_id": product_id, "options": options})
        return {
            "data": {
                "productOptionsCreate": {
                    "product": {"id": product_id},
                    "userErrors": [],
                }
            }
        }

    async def fake_bulk_create_product_variants(self, product_id, variants):
        variant_payloads.append({"product_id": product_id, "variants": variants})
        return {
            "data": {
                "productVariantsBulkCreate": {
                    "productVariants": [{"id": "gid://shopify/ProductVariant/1"}],
                    "userErrors": [],
                }
            }
        }

    import application.use_cases.processing.submit_products as submit_uc
    import api.agents.submit as submit_api

    monkeypatch.setattr(
        submit_uc, "generate_suggestions_execute", fake_generate_suggestions_execute
    )
    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "create_product_from_input",
        fake_create_product_from_input,
    )
    monkeypatch.setattr(
        submit_api.ShopifyClient, "create_product_options", fake_create_product_options
    )
    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "bulk_create_product_variants",
        fake_bulk_create_product_variants,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        payload = {
            "products_json": json.dumps([{"title": "Demo"}]),
            "import_mode": "auto",
            "enable_ai_enhancements": "true",
            "shop_domain": "store.myshopify.com",
        }
        response = await ac.post("/agents/submit-products", data=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["success_count"] == 1
        assert created_payloads[0]["vendor"] == "Enriched Vendor"
        assert created_payloads[0]["tags"] == "alpha, beta"
        assert created_payloads[0]["seo_title"] == "SEO Demo"
        assert created_payloads[0]["metafields"][0]["namespace"] == "extractor"
        assert (
            option_payloads
            and option_payloads[0]["product_id"] == "gid://shopify/Product/555"
        )
        assert (
            variant_payloads
            and variant_payloads[0]["product_id"] == "gid://shopify/Product/555"
        )


@pytest.mark.asyncio
async def test_submit_products_ai_enhancements_requires_shop_domain(monkeypatch):
    async def fail_create_product_from_input(self, product):
        _ = (self, product)
        raise AssertionError(
            "create_product_from_input should not be called without shop_domain"
        )

    import api.agents.submit as submit_api

    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "create_product_from_input",
        fail_create_product_from_input,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        payload = {
            "products_json": json.dumps([{"title": "Demo"}]),
            "import_mode": "auto",
            "enable_ai_enhancements": "true",
        }
        response = await ac.post("/agents/submit-products", data=payload)
        assert response.status_code == 400
        assert "Missing shop_domain for AI enhancements" in response.json()["detail"]


@pytest.mark.asyncio
async def test_submit_products_ai_enhancements_syncs_draft_for_preview(monkeypatch):
    async def fake_generate_suggestions_execute(
        *,
        supabase,
        products,
        shop_domain,
        normalization_settings=None,
        trace_event=None,
    ):
        _ = (supabase, products, shop_domain, normalization_settings, trace_event)
        return [
            {
                "product_index": 0,
                "patch_payload": {
                    "vendor": "Synced Vendor",
                    "tags": "synced-tag",
                    "seo_title": "Synced SEO",
                },
            }
        ]

    async def fake_create_product_from_input(self, product):
        return {
            "data": {
                "productCreate": {
                    "product": {
                        "id": "gid://shopify/Product/777",
                        "title": product.get("title"),
                    },
                    "userErrors": [],
                }
            }
        }

    import application.use_cases.processing.submit_products as submit_uc
    import api.agents.submit as submit_api

    monkeypatch.setattr(
        submit_uc, "generate_suggestions_execute", fake_generate_suggestions_execute
    )
    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "create_product_from_input",
        fake_create_product_from_input,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        created_draft = await ac.post(
            "/agents/product-drafts",
            data={
                "products_json": json.dumps([{"title": "Drafted Product"}]),
                "run_id": "run-sync-draft",
                "import_mode": "auto",
                "draft_name": "draft-sync.xlsx",
                "output_file_id": "old-output-file",
                "output_filename": "old-output.xlsx",
            },
        )
        assert created_draft.status_code == 200
        draft_id = created_draft.json()["draft_id"]

        submitted = await ac.post(
            "/agents/submit-products",
            data={
                "products_json": json.dumps([{"title": "Drafted Product"}]),
                "run_id": "run-sync-submit",
                "import_mode": "auto",
                "draft_id": draft_id,
                "document_name": "draft-sync.xlsx",
                "shop_domain": "store.myshopify.com",
                "enable_ai_enhancements": "true",
            },
        )
        assert submitted.status_code == 200

        draft_detail = await ac.get(f"/agents/product-drafts/{draft_id}")
        assert draft_detail.status_code == 200
        draft = draft_detail.json()["draft"]
        assert draft["products"][0]["vendor"] == "Synced Vendor"
        assert draft["products"][0]["seo_title"] == "Synced SEO"
        assert draft["products"][0]["tags"] == "synced-tag"
        assert draft.get("output_file_id") is None
        assert draft.get("output_filename") is None


@pytest.mark.asyncio
async def test_import_ignores_freeform_prompt_fields(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_process_document_execute(**kwargs):
        captured.update(kwargs)
        return {"run_id": "run-import", "result": {"status": "ok"}}

    import application.use_cases.processing.process_document as process_document_uc

    monkeypatch.setattr(process_document_uc, "execute", fake_process_document_execute)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        files = {
            "file": (
                "import.xlsx",
                io.BytesIO(b"sheet"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        response = await ac.post(
            "/agents/import",
            data={"prompt": "Ignore this", "writer_prompt": "Ignore this too"},
            files=files,
        )
        assert response.status_code == 200
        assert "prompt" not in captured
        assert "writer_prompt" not in captured
        assert captured.get("extraction_mode") == "per_sheet"


@pytest.mark.asyncio
async def test_list_and_get_product_draft():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        payload = {
            "products_json": json.dumps([{"title": "Draft A"}]),
            "run_id": "run-a",
            "import_mode": "create",
            "input_file_id": "input-file-a",
            "input_filename": "draft-input.xlsx",
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
        assert detail.json()["draft"]["input_file_id"] == "input-file-a"
        assert detail.json()["draft"]["input_filename"] == "draft-input.xlsx"

        resume = await ac.post(f"/agents/product-drafts/{draft_id}/resume-file")
        assert resume.status_code == 200
        resume_body = resume.json()
        assert "file_id" in resume_body
        assert resume_body["filename"].endswith(".xlsx")
        persisted_detail = await ac.get(f"/agents/product-drafts/{draft_id}")
        assert persisted_detail.status_code == 200
        persisted_draft = persisted_detail.json()["draft"]
        assert persisted_draft["output_file_id"] == resume_body["file_id"]
        assert persisted_draft["output_filename"] == resume_body["filename"]
        resume_again = await ac.post(f"/agents/product-drafts/{draft_id}/resume-file")
        assert resume_again.status_code == 200
        assert resume_again.json()["file_id"] == resume_body["file_id"]


@pytest.mark.asyncio
async def test_bulk_delete_product_drafts():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        create_payload = {
            "products_json": json.dumps([{"title": "Bulk Draft"}]),
            "run_id": "run-bulk-drafts",
            "import_mode": "create",
        }
        created = await ac.post("/agents/product-drafts", data=create_payload)
        assert created.status_code == 200
        draft_id = created.json()["draft_id"]

        missing_id = "missing-draft-id"
        bulk_response = await ac.post(
            "/agents/product-drafts/bulk-delete",
            json={"ids": [draft_id, missing_id]},
        )
        assert bulk_response.status_code == 200
        body = bulk_response.json()
        assert body["deleted_ids"] == [draft_id]
        assert body["failed_ids"] == [missing_id]
        assert (await ac.get(f"/agents/product-drafts/{draft_id}")).status_code == 404


@pytest.mark.asyncio
async def test_successful_submit_creates_submitted_and_hides_draft(monkeypatch):
    async def fake_create_product_from_input(self, product):
        return {
            "data": {
                "productCreate": {
                    "product": {
                        "id": "gid://shopify/Product/1",
                        "title": product.get("title"),
                    },
                    "userErrors": [],
                }
            }
        }

    import api.agents.submit as submit_api

    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "create_product_from_input",
        fake_create_product_from_input,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        draft_payload = {
            "products_json": json.dumps([{"title": "Submitted Draft Product"}]),
            "run_id": "run-submit",
            "import_mode": "create",
            "draft_name": "submitted-draft.xlsx",
            "input_file_id": "input-file-preview",
            "input_filename": "submitted-source.xlsx",
        }
        created_draft = await ac.post("/agents/product-drafts", data=draft_payload)
        assert created_draft.status_code == 200
        draft_id = created_draft.json()["draft_id"]

        submit_payload = {
            "products_json": json.dumps([{"title": "Submitted Draft Product"}]),
            "import_mode": "create",
            "run_id": "run-submit-1",
            "draft_id": draft_id,
            "document_name": "submitted-draft.xlsx",
        }
        submitted = await ac.post("/agents/submit-products", data=submit_payload)
        assert submitted.status_code == 200
        submitted_body = submitted.json()
        assert submitted_body["success_count"] == 1
        assert submitted_body["submitted_id"]

        drafts_after_submit = await ac.get("/agents/product-drafts")
        assert drafts_after_submit.status_code == 200
        draft_ids = [d.get("draft_id") for d in drafts_after_submit.json()["drafts"]]
        assert draft_id not in draft_ids

        submitted_list = await ac.get("/agents/submitted-documents")
        assert submitted_list.status_code == 200
        items = submitted_list.json()["submitted_documents"]
        assert any(
            item.get("submitted_id") == submitted_body["submitted_id"] for item in items
        )
        matching_item = next(
            item
            for item in items
            if item.get("submitted_id") == submitted_body["submitted_id"]
        )
        assert matching_item.get("preview_file_id") == "input-file-preview"

        submitted_detail = await ac.get(
            f"/agents/submitted-documents/{submitted_body['submitted_id']}"
        )
        assert submitted_detail.status_code == 200

        submitted_resume = await ac.post(
            f"/agents/submitted-documents/{submitted_body['submitted_id']}/resume-file"
        )
        assert submitted_resume.status_code == 200
        assert submitted_resume.json()["filename"].endswith(".xlsx")


@pytest.mark.asyncio
async def test_bulk_delete_submitted_documents(monkeypatch):
    async def fake_create_product_from_input(self, product):
        return {
            "data": {
                "productCreate": {
                    "product": {
                        "id": "gid://shopify/Product/3",
                        "title": product.get("title"),
                    },
                    "userErrors": [],
                }
            }
        }

    import api.agents.submit as submit_api

    monkeypatch.setattr(
        submit_api.ShopifyClient,
        "create_product_from_input",
        fake_create_product_from_input,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        submit_payload = {
            "products_json": json.dumps([{"title": "Bulk Submitted"}]),
            "import_mode": "create",
            "run_id": "run-bulk-submitted",
            "document_name": "bulk-submitted.xlsx",
        }
        created_submitted = await ac.post(
            "/agents/submit-products", data=submit_payload
        )
        assert created_submitted.status_code == 200
        submitted_id = created_submitted.json()["submitted_id"]

        missing_id = "missing-submitted-id"
        bulk_response = await ac.post(
            "/agents/submitted-documents/bulk-delete",
            json={"ids": [submitted_id, missing_id]},
        )
        assert bulk_response.status_code == 200
        body = bulk_response.json()
        assert body["deleted_ids"] == [submitted_id]
        assert body["failed_ids"] == [missing_id]
        assert (
            await ac.get(f"/agents/submitted-documents/{submitted_id}")
        ).status_code == 404


def _make_png_bytes(mode: str, size: tuple[int, int], color) -> bytes:
    image = Image.new(mode, size, color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_is_blank_png_detects_transparent_and_white():
    transparent_png = _make_png_bytes("RGBA", (8, 8), (0, 0, 0, 0))
    white_png = _make_png_bytes("RGBA", (8, 8), (255, 255, 255, 255))
    black_png = _make_png_bytes("RGBA", (8, 8), (0, 0, 0, 255))

    assert _is_blank_png(transparent_png) is True
    assert _is_blank_png(white_png) is True
    assert _is_blank_png(black_png) is False


@pytest.mark.asyncio
async def test_generate_thumbnail_bytes_skips_blank_pages():
    blank_transparent = _make_png_bytes("RGBA", (8, 8), (0, 0, 0, 0))
    blank_white = _make_png_bytes("RGBA", (8, 8), (255, 255, 255, 255))
    non_blank = _make_png_bytes("RGBA", (8, 8), (0, 0, 0, 255))

    class _FakeCollabora:
        async def convert_document_to_png_collabora(self, *args, **kwargs):
            _ = (args, kwargs)
            return [blank_transparent, blank_white, non_blank]

    selected = await _generate_thumbnail_bytes(
        file_bytes=b"dummy",
        filename="doc.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        collabora_url="http://localhost:8080",
        collabora=_FakeCollabora(),
    )
    assert selected == non_blank


@pytest.mark.asyncio
async def test_run_and_get_product_intelligence_audit(monkeypatch):
    async def fake_get_product(self, gid):
        return {
            "data": {
                "node": {
                    "id": gid,
                    "title": "Short",
                    "descriptionHtml": "",
                    "vendor": "Brand",
                    "handle": "short",
                    "productType": "General",
                    "status": "ACTIVE",
                    "tags": ["t1"],
                    "seo": {"title": "Short", "description": "Desc"},
                }
            }
        }

    async def fake_update_product_from_input(self, product):
        return {
            "data": {
                "productUpdate": {
                    "product": {
                        "id": product.get("id"),
                        "title": product.get("title"),
                    },
                    "userErrors": [],
                }
            }
        }

    import shopify as shopify_module

    monkeypatch.setattr(
        shopify_module.ShopifyClient,
        "update_product_from_input",
        fake_update_product_from_input,
    )
    monkeypatch.setattr(shopify_module.ShopifyClient, "get_product", fake_get_product)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        audit_run = await ac.post(
            "/agents/intelligence/audit",
            json={
                "products": [
                    {
                        "id": "gid://shopify/Product/9",
                        "title": "Short",
                        "handle": "short",
                    }
                ]
            },
        )
        assert audit_run.status_code == 200
        audit = audit_run.json()
        assert audit["audit_id"]
        assert isinstance(audit.get("overall_score"), int)
        assert isinstance(audit.get("findings"), list)

        audits = await ac.get("/agents/intelligence/audits")
        assert audits.status_code == 200
        listed = audits.json()["audits"]
        assert any(item.get("audit_id") == audit["audit_id"] for item in listed)

        detail = await ac.get(f"/agents/intelligence/audits/{audit['audit_id']}")
        assert detail.status_code == 200
        assert detail.json()["audit"]["audit_id"] == audit["audit_id"]

        suggestions = await ac.get(
            f"/agents/intelligence/audits/{audit['audit_id']}/suggestions"
        )
        assert suggestions.status_code == 200
        suggestion_rows = suggestions.json()["suggestions"]
        assert isinstance(suggestion_rows, list)
        assert suggestion_rows

        suggestion_id = suggestion_rows[0]["suggestion_id"]
        apply_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/apply"
        )
        assert apply_single.status_code == 200
        assert apply_single.json()["status"] == "applied"
        assert apply_single.json()["shopify_updated"] is True
        assert apply_single.json()["target_product_id"] == "gid://shopify/Product/9"

        revert_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/revert"
        )
        assert revert_single.status_code == 200
        assert revert_single.json()["status"] == "pending"
        assert revert_single.json()["shopify_updated"] is True
        assert revert_single.json()["target_product_id"] == "gid://shopify/Product/9"

        remaining_ids = [
            row["suggestion_id"]
            for row in suggestion_rows[1:]
            if row.get("status") != "applied"
        ]
        if remaining_ids:
            apply_bulk = await ac.post(
                "/agents/intelligence/suggestions/apply-bulk",
                json={"suggestion_ids": remaining_ids},
            )
            assert apply_bulk.status_code == 200
            assert apply_bulk.json()["failed_count"] == 0


@pytest.mark.asyncio
async def test_intelligence_audit_with_existing_products_mode(monkeypatch):
    async def fake_list_products_for_audit(self, query=None, limit=50):
        _ = (self, query, limit)
        return [
            {
                "id": "gid://shopify/Product/101",
                "title": "Catalog Product",
                "handle": "catalog-product",
                "vendor": "Brand",
                "product_type": "General",
                "status": "active",
                "tags": ["tag-1"],
                "body_html": "Catalog description",
                "seo_title": "Catalog Product",
                "seo_description": "Catalog description",
                "variants": [{"sku": "SKU-101"}],
            }
        ]

    import infrastructure.adapters.shopify_adapter as shopify_adapter

    monkeypatch.setattr(
        shopify_adapter.ShopifyAdapter,
        "list_products_for_audit",
        fake_list_products_for_audit,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        products_response = await ac.get(
            "/agents/intelligence/shopify-products?query=catalog&limit=10"
        )
        assert products_response.status_code == 200
        products = products_response.json()["products"]
        assert len(products) == 1
        assert products[0]["title"] == "Catalog Product"

        selected_audit = await ac.post(
            "/agents/intelligence/audit",
            json={"products": products},
        )
        assert selected_audit.status_code == 200
        assert selected_audit.json()["audit_id"]

        all_audit = await ac.post(
            "/agents/intelligence/audit",
            json={"all_products": True, "query": "catalog", "limit": 10},
        )
        assert all_audit.status_code == 200
        assert all_audit.json()["audit_id"]


@pytest.mark.asyncio
async def test_apply_suggestion_allows_missing_previous_values(monkeypatch):
    async def fake_get_product(self, gid):
        return {
            "data": {
                "node": {
                    "id": gid,
                    "title": "Catalog Product",
                    "descriptionHtml": "",
                    "vendor": None,
                    "handle": "catalog-product",
                    "productType": "General",
                    "status": "ACTIVE",
                    "tags": ["tag-1"],
                    "seo": {
                        "title": "Catalog Product",
                        "description": "Catalog description",
                    },
                }
            }
        }

    updates: list[dict[str, object]] = []

    async def fake_update_product_from_input(self, product):
        updates.append(dict(product))
        return {
            "data": {
                "productUpdate": {
                    "product": {"id": product.get("id")},
                    "userErrors": [],
                }
            }
        }

    import shopify as shopify_module

    monkeypatch.setattr(shopify_module.ShopifyClient, "get_product", fake_get_product)
    monkeypatch.setattr(
        shopify_module.ShopifyClient,
        "update_product_from_input",
        fake_update_product_from_input,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        audit_run = await ac.post(
            "/agents/intelligence/audit",
            json={
                "products": [
                    {
                        "id": "gid://shopify/Product/201",
                        "title": "Catalog Product",
                        "handle": "catalog-product",
                    }
                ]
            },
        )
        assert audit_run.status_code == 200
        audit_id = audit_run.json()["audit_id"]

        suggestions = await ac.get(
            f"/agents/intelligence/audits/{audit_id}/suggestions"
        )
        assert suggestions.status_code == 200
        rows = suggestions.json()["suggestions"]
        suggestion = next(
            (
                row
                for row in rows
                if isinstance(row.get("patch_payload"), dict)
                and "vendor" in row["patch_payload"]
            ),
            None,
        )
        assert suggestion is not None

        apply_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion['suggestion_id']}/apply"
        )
        assert apply_single.status_code == 200
        assert apply_single.json()["status"] == "applied"
        applied_suggestion = apply_single.json()["suggestion"]
        assert applied_suggestion["previous_payload"]["vendor"] is None
        assert applied_suggestion["previous_payload"]["__is_reversible"] is True
        assert (
            applied_suggestion["previous_payload"]["__revert_modes"]["vendor"]
            == "clear"
        )

        revert_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion['suggestion_id']}/revert"
        )
        assert revert_single.status_code == 200
        assert revert_single.json()["status"] == "pending"
        assert len(updates) == 2
        assert updates[1]["vendor"] == ""


@pytest.mark.asyncio
async def test_apply_partial_field_keeps_remaining_fields_pending(monkeypatch):
    async def fake_get_product(self, gid):
        return {
            "data": {
                "node": {
                    "id": gid,
                    "title": "Catalog Product",
                    "descriptionHtml": "Catalog description",
                    "vendor": "Original Vendor",
                    "handle": "catalog-product",
                    "productType": "General",
                    "status": "ACTIVE",
                    "tags": ["tag-1"],
                    "seo": {
                        "title": "Original SEO",
                        "description": "Original description",
                    },
                }
            }
        }

    updates: list[dict[str, object]] = []

    async def fake_update_product_from_input(self, product):
        updates.append(dict(product))
        return {
            "data": {
                "productUpdate": {
                    "product": {"id": product.get("id")},
                    "userErrors": [],
                }
            }
        }

    import shopify as shopify_module

    monkeypatch.setattr(shopify_module.ShopifyClient, "get_product", fake_get_product)
    monkeypatch.setattr(
        shopify_module.ShopifyClient,
        "update_product_from_input",
        fake_update_product_from_input,
    )

    ctx = get_app_context()
    audit_id = f"audit-partial-{uuid.uuid4()}"
    suggestion_id = f"suggestion-partial-{uuid.uuid4()}"
    ctx.services.supabase.save_product_intelligence_audit(
        audit_id=audit_id,
        run_id=None,
        submitted_id=None,
        scope="adhoc_products",
        status="success",
        overall_score=60,
        findings_count=1,
        component_scores={},
        totals={
            "audited_products": [
                {
                    "id": "gid://shopify/Product/401",
                    "title": "Catalog Product",
                    "handle": "catalog-product",
                }
            ]
        },
    )
    ctx.services.supabase.save_product_intelligence_suggestions(
        audit_id=audit_id,
        suggestions=[
            {
                "suggestion_id": suggestion_id,
                "finding_id": f"finding-{uuid.uuid4()}",
                "product_index": 0,
                "product_title": "Catalog Product",
                "category": "seo_readiness",
                "severity": "medium",
                "message": "Apply vendor and SEO title improvements",
                "patch_payload": {
                    "vendor": "Updated Vendor",
                    "seo_title": "Updated SEO Title",
                },
                "status": "pending",
            }
        ],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        apply_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/apply",
            json={"patch_payload": {"vendor": "Updated Vendor"}},
        )
        assert apply_single.status_code == 200
        assert apply_single.json()["status"] == "applied"
        assert apply_single.json()["suggestion"]["patch_payload"] == {
            "vendor": "Updated Vendor"
        }
        assert len(updates) == 1
        assert updates[0]["vendor"] == "Updated Vendor"
        assert "seo_title" not in updates[0]

        suggestions = await ac.get(
            f"/agents/intelligence/audits/{audit_id}/suggestions"
        )
        assert suggestions.status_code == 200
        rows = suggestions.json()["suggestions"]
        applied = [row for row in rows if row.get("status") == "applied"]
        pending = [row for row in rows if row.get("status") != "applied"]
        assert len(applied) == 1
        assert len(pending) == 1
        assert pending[0]["patch_payload"] == {"seo_title": "Updated SEO Title"}


@pytest.mark.asyncio
async def test_revert_blocked_for_non_reversible_missing_original_field(monkeypatch):
    async def fake_get_product(self, gid):
        return {
            "data": {
                "node": {
                    "id": gid,
                    "title": "Catalog Product",
                    "descriptionHtml": "",
                    "vendor": "Brand",
                    "handle": "catalog-product",
                    "productType": "General",
                    "status": "ACTIVE",
                    "tags": ["tag-1"],
                    "seo": {
                        "title": "Catalog Product",
                        "description": "Catalog description",
                    },
                }
            }
        }

    async def fake_update_product_from_input(self, product):
        _ = product
        return {
            "data": {
                "productUpdate": {
                    "product": {"id": "gid://shopify/Product/301"},
                    "userErrors": [],
                }
            }
        }

    import shopify as shopify_module

    monkeypatch.setattr(shopify_module.ShopifyClient, "get_product", fake_get_product)
    monkeypatch.setattr(
        shopify_module.ShopifyClient,
        "update_product_from_input",
        fake_update_product_from_input,
    )

    ctx = get_app_context()
    audit_id = f"audit-nonreversible-{uuid.uuid4()}"
    suggestion_id = f"suggestion-nonreversible-{uuid.uuid4()}"
    ctx.services.supabase.save_product_intelligence_audit(
        audit_id=audit_id,
        run_id=None,
        submitted_id=None,
        scope="adhoc_products",
        status="success",
        overall_score=60,
        findings_count=1,
        component_scores={},
        totals={
            "audited_products": [
                {
                    "id": "gid://shopify/Product/301",
                    "title": "Catalog Product",
                    "handle": "catalog-product",
                }
            ]
        },
    )
    ctx.services.supabase.save_product_intelligence_suggestions(
        audit_id=audit_id,
        suggestions=[
            {
                "suggestion_id": suggestion_id,
                "finding_id": f"finding-{uuid.uuid4()}",
                "product_index": 0,
                "product_title": "Catalog Product",
                "category": "consistency",
                "severity": "low",
                "message": "Apply unsupported field fix",
                "patch_payload": {"nonexistent_field": "value"},
                "status": "pending",
            }
        ],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        apply_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/apply"
        )
        assert apply_single.status_code == 200
        applied_suggestion = apply_single.json()["suggestion"]
        assert applied_suggestion["previous_payload"]["__is_reversible"] is False

        revert_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/revert"
        )
        assert revert_single.status_code == 400
        assert "Auto-revert is unavailable" in revert_single.json()["detail"]
