import pytest
import respx

from shopify import ShopifyClient


@pytest.mark.asyncio
async def test_shopify_client_crud():
    client = ShopifyClient(shop="test-shop.myshopify.com", token="token")
    url = client.url

    create_resp = {"data": {"productCreate": {"product": {"id": "gid://shopify/Product/1", "title": "T", "handle": "h"}, "userErrors": []}}}
    get_resp = {"data": {"node": {"id": "gid://shopify/Product/1", "title": "T"}}}
    update_resp = {"data": {"productUpdate": {"product": {"id": "gid://shopify/Product/1", "title": "T updated", "handle": "h"}, "userErrors": []}}}
    delete_resp = {"data": {"productDelete": {"deletedProductId": "gid://shopify/Product/1", "userErrors": []}}}

    with respx.mock(base_url="https://test-shop.myshopify.com") as mock:
        mock.post("/admin/api/2024-10/graphql.json").respond(200, json=create_resp)
        resp = await client.create_product("T", "", None)
        assert resp["data"]["productCreate"]["product"]["title"] == "T"

        mock.post("/admin/api/2024-10/graphql.json").respond(200, json=get_resp)
        resp = await client.get_product("gid://shopify/Product/1")
        assert resp["data"]["node"]["id"].endswith("/1")

        mock.post("/admin/api/2024-10/graphql.json").respond(200, json=update_resp)
        resp = await client.update_product("gid://shopify/Product/1", "T updated", None)
        assert resp["data"]["productUpdate"]["product"]["title"] == "T updated"

        mock.post("/admin/api/2024-10/graphql.json").respond(200, json=delete_resp)
        resp = await client.delete_product("gid://shopify/Product/1")
        assert resp["data"]["productDelete"]["deletedProductId"].endswith("/1")
