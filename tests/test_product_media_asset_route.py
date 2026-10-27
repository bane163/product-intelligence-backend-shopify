import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from main import app


@pytest.mark.asyncio
async def test_product_media_asset_is_image_only_and_tenant_scoped(monkeypatch):
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    asset_id = f"product-media-{uuid.uuid4()}.jpg"
    service.save_file(
        asset_id,
        name=asset_id,
        content=b"reviewed-image",
        content_type="image/jpeg",
        file_origin="product_media_asset",
        shop_domain="media-owner.myshopify.com",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        allowed = await client.get(
            f"/agents/media-assets/{asset_id}",
            headers={"x-shop-domain": "media-owner.myshopify.com"},
        )
        denied = await client.get(
            f"/agents/media-assets/{asset_id}",
            headers={"x-shop-domain": "different-shop.myshopify.com"},
        )

    assert allowed.status_code == 200
    assert allowed.content == b"reviewed-image"
    assert allowed.headers["content-type"] == "image/jpeg"
    assert allowed.headers["x-content-type-options"] == "nosniff"
    assert denied.status_code == 404
