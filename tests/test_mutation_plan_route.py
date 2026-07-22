import re

import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from main import app
from application.domain.shopify_product_normalization import build_product_set_input


def test_mutation_plan_queries_use_inventory_item_unit_cost_output():
    import api.agents.mutation_plans as mutation_plans_api

    for query in (
        mutation_plans_api.PRODUCT_BY_HANDLE_QUERY,
        mutation_plans_api.PRODUCT_VARIANTS_PAGE_QUERY,
    ):
        assert "unitCost { amount }" in query
        assert not re.search(r"\bcost\b", query)


@pytest.mark.asyncio
async def test_preflight_fetches_paginated_variants_with_unit_cost_query(monkeypatch):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "mutation-plan-paginated-variants"
    shop_domain = "pagination-test.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [{"title": "Paged product", "handle": "paged-product"}],
    }
    calls = []

    class FakeShopifyClient:
        async def graphql(self, query, variables=None):
            calls.append((query, variables))
            if query == mutation_plans_api.ACCESS_SCOPES_QUERY:
                return {
                    "data": {
                        "currentAppInstallation": {
                            "accessScopes": [{"handle": "write_products"}]
                        }
                    }
                }
            if query == mutation_plans_api.PRODUCT_BY_HANDLE_QUERY:
                return {
                    "data": {
                        "products": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/Product/1",
                                    "title": "Paged product",
                                    "handle": "paged-product",
                                    "options": [],
                                    "variants": {
                                        "nodes": [
                                            {"id": "gid://shopify/ProductVariant/1"}
                                        ],
                                        "pageInfo": {
                                            "hasNextPage": True,
                                            "endCursor": "page-1",
                                        },
                                    },
                                }
                            ]
                        }
                    }
                }
            assert query == mutation_plans_api.PRODUCT_VARIANTS_PAGE_QUERY
            assert variables == {
                "id": "gid://shopify/Product/1",
                "after": "page-1",
            }
            return {
                "data": {
                    "product": {
                        "variants": {
                            "nodes": [{"id": "gid://shopify/ProductVariant/2"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }

        async def close(self):
            return None

    async def fake_offline_client(shop):
        assert shop == shop_domain
        return FakeShopifyClient()

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        response = await client.post(f"/agents/product-drafts/{draft_id}/mutation-plan")

    assert response.status_code == 200
    assert (
        len(response.json()["plan"]["items"][0]["live_snapshot"]["variants"]["nodes"])
        == 2
    )
    assert [query for query, _ in calls].count(
        mutation_plans_api.PRODUCT_VARIANTS_PAGE_QUERY
    ) == 1


@pytest.mark.asyncio
async def test_create_mutation_plan_returns_review_plan_and_closes_client(monkeypatch):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "mutation-plan-close-regression"
    shop_domain = "review-test.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [{"title": "Reviewable product"}],
    }

    class FakeShopifyClient:
        def __init__(self):
            self.closed = False

        async def graphql(self, query, variables=None):
            assert query == mutation_plans_api.ACCESS_SCOPES_QUERY
            assert variables is None
            return {
                "data": {
                    "currentAppInstallation": {
                        "accessScopes": [{"handle": "write_products"}]
                    },
                    "shop": {"currencyCode": "CAD"},
                }
            }

        async def close(self):
            self.closed = True

    fake_client = FakeShopifyClient()

    async def fake_offline_client(shop):
        assert shop == shop_domain
        return fake_client

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        response = await client.post(f"/agents/product-drafts/{draft_id}/mutation-plan")

    assert response.status_code == 200
    assert response.json()["plan"]["status"] == "review"
    assert response.json()["plan"]["items"][0]["operation"] == "create"
    item = response.json()["plan"]["items"][0]
    original_proposed = item["proposed"]
    original_review = item["review_changes"]
    assert response.json()["plan"]["currency_code"] == "CAD"
    assert item["selected"] is True
    assert item["selectable"] is True
    assert item["applicable_changes"]
    assert fake_client.closed is True

    plan_id = response.json()["plan"]["plan_id"]
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        deselected = await client.patch(
            f"/agents/product-drafts/{draft_id}/mutation-plan/selection",
            json={"plan_id": plan_id, "selected_item_ids": []},
        )
        rejected = await client.post(
            f"/agents/product-drafts/{draft_id}/mutation-plan/approve",
            json={"plan_id": plan_id, "acknowledge_unapplied": True},
        )
    assert deselected.status_code == 200
    assert deselected.json()["plan"]["items"][0]["selected"] is False
    assert deselected.json()["plan"]["items"][0]["proposed"] == original_proposed
    assert all(
        row["included"] is False
        for row in deselected.json()["plan"]["items"][0]["review_changes"]
        if row["disposition"] == "applicable"
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "REVIEW_REQUIRED"

    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        reselected = await client.patch(
            f"/agents/product-drafts/{draft_id}/mutation-plan/selection",
            json={"plan_id": plan_id, "selected_item_ids": [item["item_id"]]},
        )
        approved = await client.post(
            f"/agents/product-drafts/{draft_id}/mutation-plan/approve",
            json={"plan_id": plan_id, "acknowledge_unapplied": True},
        )
    assert reselected.status_code == 200
    assert approved.status_code == 200
    assert approved.json()["plan"]["items"][0]["proposed"] == original_proposed
    assert {
        row["change_id"] for row in approved.json()["plan"]["items"][0]["review_changes"]
    } == {row["change_id"] for row in original_review}
    assert all(
        row["included"] is True
        for row in approved.json()["plan"]["items"][0]["review_changes"]
        if row["disposition"] == "applicable"
    )


@pytest.mark.asyncio
async def test_field_selection_enforces_required_rows_and_persists_optional_exclusion(monkeypatch):
    draft_id = "field-selection-contract"
    plan_id = "field-selection-plan"
    shop_domain = "field-selection.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [{"title": "New product", "vendor": "Optional vendor"}],
    }
    service.mutation_plans[plan_id] = {
        "plan_id": plan_id,
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "draft_revision": 1,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "item-1",
            "source_index": 0,
            "selectable": True,
            "selected": True,
            "review_changes": [
                {"change_id": "required-title", "disposition": "applicable", "required": True, "selectable": False, "included": True},
                {"change_id": "optional-vendor", "disposition": "applicable", "required": False, "selectable": True, "included": True},
            ],
        }],
    }
    url = f"/agents/product-drafts/{draft_id}/mutation-plan/selection"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        rejected = await client.patch(url, json={
            "plan_id": plan_id,
            "item_selections": [{"item_id": "item-1", "included": True, "included_change_ids": ["optional-vendor"]}],
        })
        accepted = await client.patch(url, json={
            "plan_id": plan_id,
            "item_selections": [{"item_id": "item-1", "included": True, "included_change_ids": ["required-title"]}],
        })

    assert rejected.status_code == 409
    assert accepted.status_code == 200
    rows = accepted.json()["plan"]["items"][0]["review_changes"]
    assert {row["change_id"]: row["included"] for row in rows} == {
        "required-title": True,
        "optional-vendor": False,
    }


@pytest.mark.asyncio
async def test_preflight_keeps_aliased_core_updates_selectable_and_serializable(
    monkeypatch,
):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "mutation-plan-aliases"
    shop_domain = "aliases-test.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [
            {
                "title": "Catalog Hat",
                "handle": "catalog-hat",
                "body_html": "<p>New description</p>",
                "product_category": "Accessories",
                "seo_title": "New SEO title",
                "seo_description": "New SEO description",
                "variants": [{"sku": "deferred"}],
            }
        ],
    }

    class FakeShopifyClient:
        async def graphql(self, query, variables=None):
            if query == mutation_plans_api.ACCESS_SCOPES_QUERY:
                return {
                    "data": {
                        "currentAppInstallation": {
                            "accessScopes": [{"handle": "write_products"}]
                        }
                    }
                }
            assert query == mutation_plans_api.PRODUCT_BY_HANDLE_QUERY
            return {
                "data": {
                    "products": {
                        "nodes": [
                            {
                                "id": "gid://shopify/Product/1",
                                "title": "Catalog Hat",
                                "handle": "catalog-hat",
                                "descriptionHtml": "<p>Old description</p>",
                                "vendor": "",
                                "productType": "Clothing",
                                "status": "DRAFT",
                                "tags": [],
                                "seo": {
                                    "title": "Old SEO title",
                                    "description": "Old SEO description",
                                },
                            }
                        ]
                    }
                }
            }

        async def close(self):
            return None

    async def fake_offline_client(shop):
        assert shop == shop_domain
        return FakeShopifyClient()

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        response = await client.post(f"/agents/product-drafts/{draft_id}/mutation-plan")

    assert response.status_code == 200
    item = response.json()["plan"]["items"][0]
    assert item["operation"] == "update"
    assert item["selectable"] is True
    assert item["selected"] is True
    assert {change["field"] for change in item["applicable_changes"]} == {
        "descriptionHtml",
        "productType",
        "seo",
        "options",
        "variants",
    }
    assert item["unapplied"] == []
    assert item["proposed"]["productOptions"][0]["name"] == "Title"
    assert item["proposed"]["variants"][0]["sku"] == "deferred"
    payload = build_product_set_input(item["proposed"])
    assert payload["descriptionHtml"] == "<p>New description</p>"
    assert payload["productType"] == "Accessories"
    assert payload["seo"] == {
        "title": "New SEO title",
        "description": "New SEO description",
    }
    assert payload["variants"][0]["sku"] == "deferred"


@pytest.mark.asyncio
async def test_structured_only_trail_tee_mug_and_daypack_updates_start_selected(
    monkeypatch,
):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "structured-catalog-fixtures"
    shop_domain = "structured-test.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    products = []
    for handle, title in (
        ("trail-tee", "Trail Tee"),
        ("ceramic-mug", "Ceramic Mug"),
        ("daypack", "Daypack"),
    ):
        products.append(
            {
                "title": title,
                "handle": handle,
                "status": "DRAFT",
                "options": [{"name": "Color", "values": ["Black", "Clay"]}],
                "variants": [
                    {
                        "option_values": [{"option_name": "Color", "name": "Black"}],
                        "sku": f"{handle}-BLACK",
                        "price": "24",
                    },
                    {
                        "option_values": [{"option_name": "Color", "name": "Clay"}],
                        "sku": f"{handle}-CLAY",
                        "price": "26",
                        "inventory_quantity": 5,
                    },
                ],
                "metafields": [
                    {
                        "namespace": "custom",
                        "key": "collection",
                        "value": "summer",
                        "type": "single_line_text_field",
                    }
                ],
            }
        )
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": products,
    }

    class FakeShopifyClient:
        async def graphql(self, query, variables=None):
            if query == mutation_plans_api.ACCESS_SCOPES_QUERY:
                return {
                    "data": {
                        "currentAppInstallation": {
                            "accessScopes": [{"handle": "write_products"}]
                        }
                    }
                }
            assert query == mutation_plans_api.PRODUCT_BY_HANDLE_QUERY
            handle = str(variables["query"]).split(":", 1)[1]
            title = next(
                product["title"] for product in products if product["handle"] == handle
            )
            return {
                "data": {
                    "products": {
                        "nodes": [
                            {
                                "id": f"gid://shopify/Product/{handle}",
                                "title": title,
                                "handle": handle,
                                "status": "DRAFT",
                                "tags": [],
                                "seo": {},
                                "options": [
                                    {
                                        "name": "Title",
                                        "position": 1,
                                        "optionValues": [{"name": "Default Title"}],
                                    }
                                ],
                                "variants": {
                                    "nodes": [
                                        {
                                            "id": f"gid://shopify/ProductVariant/{handle}",
                                            "sku": "",
                                            "price": "0",
                                            "selectedOptions": [
                                                {
                                                    "name": "Title",
                                                    "value": "Default Title",
                                                }
                                            ],
                                        }
                                    ],
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                },
                                "metafields": {"nodes": []},
                            }
                        ]
                    }
                }
            }

        async def close(self):
            return None

    async def fake_offline_client(shop):
        assert shop == shop_domain
        return FakeShopifyClient()

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        response = await client.post(f"/agents/product-drafts/{draft_id}/mutation-plan")

    assert response.status_code == 200
    items = response.json()["plan"]["items"]
    assert len(items) == 3
    assert all(
        item["operation"] == "update" and item["selectable"] and item["selected"]
        for item in items
    )
    assert all(
        {"options", "variants", "metafields"}
        <= {change["field"] for change in item["applicable_changes"]}
        for item in items
    )
    assert all(
        item["unapplied"]
        == [
            {
                "code": "TODO_FULFILLMENT_LOCATION",
                "field": "variants[1].inventory_quantity",
                "value": 5,
            }
        ]
        for item in items
    )
