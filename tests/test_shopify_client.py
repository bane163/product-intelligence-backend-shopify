import json

import pytest
import respx
import httpx

from shopify import ShopifyClient


@pytest.mark.asyncio
async def test_shopify_client_crud():
    client = ShopifyClient(shop="test-shop.myshopify.com", token="token")
    url = client.url

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
    get_resp = {"data": {"node": {"id": "gid://shopify/Product/1", "title": "T"}}}
    update_resp = {
        "data": {
            "productUpdate": {
                "product": {
                    "id": "gid://shopify/Product/1",
                    "title": "T updated",
                    "handle": "h",
                },
                "userErrors": [],
            }
        }
    }
    delete_resp = {
        "data": {
            "productDelete": {
                "deletedProductId": "gid://shopify/Product/1",
                "userErrors": [],
            }
        }
    }

    with respx.mock(base_url="https://test-shop.myshopify.com") as mock:
        mock.post("/admin/api/2025-10/graphql.json").respond(200, json=create_resp)
        resp = await client.create_product("T", "", None)
        assert resp["data"]["productCreate"]["product"]["title"] == "T"

        mock.post("/admin/api/2025-10/graphql.json").respond(200, json=get_resp)
        resp = await client.get_product("gid://shopify/Product/1")
        assert resp["data"]["node"]["id"].endswith("/1")

        mock.post("/admin/api/2025-10/graphql.json").respond(200, json=update_resp)
        resp = await client.update_product("gid://shopify/Product/1", "T updated", None)
        assert resp["data"]["productUpdate"]["product"]["title"] == "T updated"

        mock.post("/admin/api/2025-10/graphql.json").respond(200, json=delete_resp)
        resp = await client.delete_product("gid://shopify/Product/1")
        assert resp["data"]["productDelete"]["deletedProductId"].endswith("/1")


@pytest.mark.asyncio
async def test_update_product_with_metafields_triggers_metafields_set():
    client = ShopifyClient(shop="test-shop.myshopify.com", token="token")

    update_resp = {
        "data": {
            "productUpdate": {
                "product": {
                    "id": "gid://shopify/Product/1",
                    "title": "T updated",
                    "handle": "h",
                },
                "userErrors": [],
            }
        }
    }
    metafields_resp = {
        "data": {
            "metafieldsSet": {
                "metafields": [
                    {
                        "id": "gid://shopify/Metafield/1",
                        "namespace": "specbrain",
                        "key": "material",
                        "value": "cotton",
                        "type": "single_line_text_field",
                    }
                ],
                "userErrors": [],
            }
        }
    }

    with respx.mock(base_url="https://test-shop.myshopify.com") as mock:
        route = mock.post("/admin/api/2025-10/graphql.json")
        route.side_effect = [
            httpx.Response(200, json=update_resp),
            httpx.Response(200, json=metafields_resp),
        ]
        resp = await client.update_product_from_input(
            {
                "id": "gid://shopify/Product/1",
                "title": "T updated",
                "seo_title": "Better title",
                "metafields": [
                    {
                        "namespace": "specbrain",
                        "key": "material",
                        "value": "cotton",
                        "type": "single_line_text_field",
                    }
                ],
            }
        )
        assert resp["data"]["productUpdate"]["product"]["title"] == "T updated"
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_list_products_for_audit_returns_normalized_rows():
    client = ShopifyClient(shop="test-shop.myshopify.com", token="token")

    search_resp = {
        "data": {
            "products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {
                        "cursor": "c1",
                        "node": {
                            "id": "gid://shopify/Product/101",
                            "title": "Catalog Product",
                            "handle": "catalog-product",
                            "vendor": "Brand",
                            "productType": "General",
                            "status": "ACTIVE",
                            "tags": ["t1"],
                            "descriptionHtml": "<p>Desc</p>",
                            "seo": {"title": "SEO", "description": "SEO Desc"},
                            "variants": {"nodes": [{"sku": "SKU-101"}]},
                        },
                    }
                ],
            }
        }
    }

    with respx.mock(base_url="https://test-shop.myshopify.com") as mock:
        mock.post("/admin/api/2025-10/graphql.json").respond(200, json=search_resp)
        rows = await client.list_products_for_audit(query="catalog", limit=10)
        assert len(rows) == 1
        assert rows[0]["title"] == "Catalog Product"
        assert rows[0]["product_type"] == "General"


@pytest.mark.asyncio
async def test_update_product_from_input_supports_explicit_clear_payloads():
    client = ShopifyClient(shop="test-shop.myshopify.com", token="token")
    update_resp = {
        "data": {
            "productUpdate": {
                "product": {"id": "gid://shopify/Product/1"},
                "userErrors": [],
            }
        }
    }

    with respx.mock(base_url="https://test-shop.myshopify.com") as mock:
        route = mock.post("/admin/api/2025-10/graphql.json").respond(200, json=update_resp)
        await client.update_product_from_input(
            {
                "id": "gid://shopify/Product/1",
                "vendor": "",
                "body_html": "",
                "product_type": "",
                "tags": [],
                "seo_title": "",
                "seo_description": "",
            }
        )
        assert route.called
        request_body = json.loads(route.calls.last.request.content.decode())
        product = request_body["variables"]["product"]
        assert product["vendor"] == ""
        assert product["descriptionHtml"] == ""
        assert product["productType"] == ""
        assert product["tags"] == []
        assert product["seo"] == {"title": "", "description": ""}
