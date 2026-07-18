import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from main import app


@pytest.mark.asyncio
async def test_imports_summary_is_empty_for_new_shop():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-shop-domain": "empty.myshopify.com"},
    ) as client:
        response = await client.get("/agents/imports/summary")

    assert response.status_code == 200
    assert response.json() == {"uploaded_files": 0, "drafts": 0, "submitted": 0}


@pytest.mark.asyncio
async def test_imports_summary_counts_only_request_shop():
    service = get_app_context().services.supabase._service
    for shop, suffix in (("alpha.myshopify.com", "a"), ("beta.myshopify.com", "b")):
        service.file_storage[f"file-{suffix}"] = {
            "name": f"supplier-{suffix}.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "file_origin": "merchant_upload",
            "shop_domain": shop,
        }
        service.product_drafts[f"draft-{suffix}"] = {
            "draft_id": f"draft-{suffix}", "products": [], "shop_domain": shop,
        }
        service.submitted_documents[f"submitted-{suffix}"] = {
            "submitted_id": f"submitted-{suffix}", "products": [], "shop_domain": shop,
        }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-shop-domain": "alpha.myshopify.com"},
    ) as client:
        response = await client.get("/agents/imports/summary")

    assert response.status_code == 200
    assert response.json() == {"uploaded_files": 1, "drafts": 1, "submitted": 1}
