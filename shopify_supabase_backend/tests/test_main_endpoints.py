import os
import pytest
import respx
from httpx import AsyncClient

# Ensure env vars are present before importing the app
os.environ.setdefault("SHOPIFY_STORE", "test-shop.myshopify.com")
os.environ.setdefault("SHOPIFY_ACCESS_TOKEN", "token")

from shopify_supabase_backend.main import app


@pytest.mark.asyncio
async def test_create_product_endpoint():
    url = "https://test-shop.myshopify.com/admin/api/2024-10/graphql.json"
    create_resp = {"data": {"productCreate": {"product": {"id": "gid://shopify/Product/1", "title": "T", "handle": "h"}, "userErrors": []}}}

    with respx.mock(base_url="https://test-shop.myshopify.com") as mock:
        mock.post("/admin/api/2024-10/graphql.json").respond(200, json=create_resp)
        async with AsyncClient(app=app, base_url="http://testserver") as ac:
            r = await ac.post("/shopify/products", json={"title": "T"})
            assert r.status_code == 200
            data = r.json()
            assert data["product"]["title"] == "T"
