import os
import pytest
import respx
from httpx import AsyncClient, ASGITransport

# Ensure env vars are present before importing the app
os.environ["SHOPIFY_STORE"] = "test-shop.myshopify.com"
os.environ["SHOPIFY_ACCESS_TOKEN"] = "token"

from main import app
from api.shopify_products import client as shopify_client


@pytest.mark.asyncio
async def test_create_product_endpoint():
    shopify_client.shop = "test-shop.myshopify.com"
    shopify_client.url = "https://test-shop.myshopify.com/admin/api/2025-10/graphql.json"
    shopify_client.set_token("token")

    create_resp = {
        "data": {
            "productCreate": {
                "product": {
                    "id": "gid://shopify/Product/1",
                    "title": "T",
                    "handle": "h",
                },
                "userErrors": [],
            }
        }
    }

    with respx.mock(base_url="https://test-shop.myshopify.com") as mock:
        mock.post("/admin/api/2025-10/graphql.json").respond(200, json=create_resp)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            r = await ac.post("/shopify/products/", json={"title": "T"})
            assert r.status_code == 200
            data = r.json()
            assert data["product"]["title"] == "T"
