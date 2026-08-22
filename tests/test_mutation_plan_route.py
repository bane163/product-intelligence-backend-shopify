import re

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

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


def test_location_query_and_view_use_location_address_country_code():
    import api.agents.mutation_plans as mutation_plans_api

    assert "countryCode" in mutation_plans_api.LOCATIONS_QUERY
    assert "countryCodeV2" not in mutation_plans_api.LOCATIONS_QUERY
    assert mutation_plans_api._location_view(
        {
            "id": "gid://shopify/Location/1",
            "name": "Main",
            "address": {
                "address1": "123 Main St",
                "city": "New York",
                "provinceCode": "NY",
                "zip": "10001",
                "countryCode": "US",
            },
        }
    ) == {
        "id": "gid://shopify/Location/1",
        "name": "Main",
        "address": ["123 Main St", "New York, NY, 10001", "US"],
    }


@pytest.mark.asyncio
async def test_create_mutation_plan_direct_call_defaults_to_plain_payload(monkeypatch):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "mutation-plan-direct-call"
    shop_domain = "direct-call.myshopify.com"
    context = get_app_context()
    service = context.services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [{"title": "Direct call product"}],
    }

    class FakeShopifyClient:
        async def graphql(self, query, variables=None):
            if query == mutation_plans_api.ACCESS_SCOPES_QUERY:
                return {
                    "data": {
                        "currentAppInstallation": {
                            "accessScopes": [{"handle": "write_products"}]
                        },
                        "shop": {"currencyCode": "USD"},
                    }
                }
            assert query == mutation_plans_api.VARIANTS_BY_SKU_QUERY
            return {"data": {"productVariants": {"nodes": []}}}

        async def close(self):
            return None

    async def fake_offline_client(shop):
        assert shop == shop_domain
        return FakeShopifyClient()

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "query_string": b"",
        "headers": [(b"x-shop-domain", shop_domain.encode())],
    })

    response = await mutation_plans_api.create_mutation_plan(
        draft_id=draft_id,
        request=request,
        ctx=context,
    )

    assert response["plan"]["draft_revision"] == 1


@pytest.mark.asyncio
async def test_approval_requires_acknowledgment_for_selected_quality_warnings(
    monkeypatch,
):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "quality-warning-approval"
    plan_id = "quality-warning-plan"
    shop_domain = "quality-warning.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [{"title": "Warning product"}],
    }
    service.mutation_plans[plan_id] = {
        "plan_id": plan_id,
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "draft_revision": 1,
        "version": 1,
        "status": "review",
        "review_contract_version": 3,
        "items": [
            {
                "item_id": "warning-item",
                "selected": True,
                "selectable": True,
                "blockers": [],
                "proposed": {"title": "Warning product"},
                "review_changes": [],
                "quality_issues": [
                    {
                        "issue_id": "vendor_missing:product-0-vendor",
                        "severity": "warning",
                        "code": "VENDOR_MISSING",
                        "message": "No vendor was extracted.",
                    }
                ],
            }
        ],
    }

    class FakeApprovalClient:
        async def close(self):
            return None

    async def fake_offline_client(shop):
        assert shop == shop_domain
        return FakeApprovalClient()

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)
    url = f"/agents/product-drafts/{draft_id}/mutation-plan/approve"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        rejected = await client.post(url, json={"plan_id": plan_id})
        approved = await client.post(
            url, json={"plan_id": plan_id, "warnings_acknowledged": True}
        )

    assert rejected.status_code == 422
    assert (
        rejected.json()["detail"]["code"] == "QUALITY_WARNINGS_ACKNOWLEDGMENT_REQUIRED"
    )
    assert approved.status_code == 200
    assert approved.json()["plan"]["warnings_acknowledged_at"]
    assert approved.json()["plan"]["warnings_acknowledgment_hash"]


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
        "products": [
            {
                "title": "Reviewable product",
                "vendor": "Acme",
                "image_src": "https://example.com/product.jpg",
                "variants": [{"sku": "REVIEW-1", "price": 10}],
                "source_refs": [{"value_path": "title", "cell": "A2"}],
            }
        ],
    }

    class FakeShopifyClient:
        def __init__(self):
            self.closed = False

        async def graphql(self, query, variables=None):
            if query == mutation_plans_api.VARIANTS_BY_SKU_QUERY:
                return {"data": {"productVariants": {"nodes": []}}}
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
            json={
                "plan_id": plan_id,
                "acknowledge_unapplied": True,
                "warnings_acknowledged": True,
            },
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
            json={
                "plan_id": plan_id,
                "acknowledge_unapplied": True,
                "warnings_acknowledged": True,
            },
        )
    assert reselected.status_code == 200
    assert approved.status_code == 200
    assert approved.json()["plan"]["items"][0]["proposed"] == original_proposed
    assert {
        row["change_id"]
        for row in approved.json()["plan"]["items"][0]["review_changes"]
    } == {row["change_id"] for row in original_review}
    assert all(
        row["included"] is True
        for row in approved.json()["plan"]["items"][0]["review_changes"]
        if row["disposition"] == "applicable"
    )


@pytest.mark.asyncio
async def test_field_selection_enforces_required_rows_and_persists_optional_exclusion(
    monkeypatch,
):
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
        "items": [
            {
                "item_id": "item-1",
                "source_index": 0,
                "selectable": True,
                "selected": True,
                "review_changes": [
                    {
                        "change_id": "required-title",
                        "disposition": "applicable",
                        "required": True,
                        "selectable": False,
                        "included": True,
                    },
                    {
                        "change_id": "optional-vendor",
                        "disposition": "applicable",
                        "required": False,
                        "selectable": True,
                        "included": True,
                    },
                ],
            }
        ],
    }
    url = f"/agents/product-drafts/{draft_id}/mutation-plan/selection"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        rejected = await client.patch(
            url,
            json={
                "plan_id": plan_id,
                "item_selections": [
                    {
                        "item_id": "item-1",
                        "included": True,
                        "included_change_ids": ["optional-vendor"],
                    }
                ],
            },
        )
        accepted = await client.patch(
            url,
            json={
                "plan_id": plan_id,
                "item_selections": [
                    {
                        "item_id": "item-1",
                        "included": True,
                        "included_change_ids": ["required-title"],
                    }
                ],
            },
        )

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
                "variants": [{"sku": "deferred", "price": 10}],
            }
        ],
    }

    class FakeShopifyClient:
        async def graphql(self, query, variables=None):
            if query == mutation_plans_api.ACCESS_SCOPES_QUERY:
                return {
                    "data": {
                        "currentAppInstallation": {
                            "accessScopes": [
                                {"handle": "write_products"},
                                {"handle": "write_inventory"},
                                {"handle": "read_inventory"},
                                {"handle": "read_locations"},
                            ]
                        }
                    }
                }
            if query == mutation_plans_api.LOCATIONS_QUERY:
                return {
                    "data": {
                        "locations": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/Location/1",
                                    "name": "Main",
                                    "isActive": True,
                                    "isFulfillmentService": False,
                                    "address": {},
                                }
                            ]
                        }
                    }
                }
            if query == mutation_plans_api.INVENTORY_SNAPSHOT_QUERY:
                return {"data": {"product": {"variants": {"nodes": []}}}}
            if query == mutation_plans_api.VARIANTS_BY_SKU_QUERY:
                return {"data": {"productVariants": {"nodes": []}}}
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
                            "accessScopes": [
                                {"handle": "write_products"},
                                {"handle": "write_inventory"},
                                {"handle": "read_inventory"},
                                {"handle": "read_locations"},
                            ]
                        }
                    }
                }
            if query == mutation_plans_api.LOCATIONS_QUERY:
                return {
                    "data": {
                        "locations": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/Location/1",
                                    "name": "Main",
                                    "isActive": True,
                                    "isFulfillmentService": False,
                                    "address": {},
                                }
                            ]
                        }
                    }
                }
            if query == mutation_plans_api.INVENTORY_SNAPSHOT_QUERY:
                return {"data": {"product": {"variants": {"nodes": []}}}}
            if query == mutation_plans_api.VARIANTS_BY_SKU_QUERY:
                return {"data": {"productVariants": {"nodes": []}}}
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
    assert response.json()["plan"]["review_contract_version"] == 5
    assert all(item["review_preview"]["variants"] for item in items)
    assert response.json()["plan"]["inventory_context"]["status"] == "ready"
    assert all(item["unapplied"] == [] for item in items)
    assert all(
        any(row["field"] == "inventory_quantity" for row in item["review_changes"])
        for item in items
    )


def test_stored_v2_plans_and_edit_sessions_hydrate_safe_row_defaults(monkeypatch):
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    stored = {
        "plan_id": "stored-row-presentation-plan",
        "review_contract_version": 2,
        "items": [
            {
                "proposed": {"productOptions": []},
                "review_changes": [
                    {
                        "scope": "option",
                        "subject": "Title",
                        "field": "values",
                        "disposition": "applicable",
                    }
                ],
            }
        ],
    }

    hydrated_plan = service._hydrate_plan(stored)
    hydrated_session = service._hydrate_review_edit_session(
        {
            "session_id": "stored-row-presentation-session",
            "working_plan": stored,
        }
    )

    plan_row = hydrated_plan["items"][0]["review_changes"][0]
    session_row = hydrated_session["working_plan"]["items"][0]["review_changes"][0]
    assert plan_row["presentation"] == "hidden"
    assert plan_row["editable"] is False
    assert session_row["presentation"] == "hidden"
    assert session_row["editable"] is False


@pytest.mark.asyncio
async def test_approval_ignores_legacy_acknowledgement_and_omits_deferred_values(
    monkeypatch,
):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "deferred-approval-without-acknowledgement"
    plan_id = "deferred-approval-plan"
    shop_domain = "deferred-approval.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [{"title": "Safe product"}],
    }
    service.mutation_plans[plan_id] = {
        "plan_id": plan_id,
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "draft_revision": 1,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [
            {
                "item_id": "deferred-item",
                "source_index": 0,
                "selected": True,
                "selectable": True,
                "blockers": [],
                "unapplied": [
                    {
                        "code": "TODO_FULFILLMENT_LOCATION",
                        "field": "variants[0].inventory_quantity",
                        "value": 7,
                    }
                ],
                "proposed": {
                    "title": "Safe product",
                    "variants": [{"sku": "SAFE-SKU"}],
                },
                "review_changes": [
                    {
                        "change_id": "deferred-quantity",
                        "scope": "inventory",
                        "subject": "Variant 1",
                        "field": "inventory_quantity",
                        "proposed": 7,
                        "disposition": "deferred",
                        "included": False,
                    }
                ],
            }
        ],
    }

    class FakeShopifyClient:
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
        response = await client.post(
            f"/agents/product-drafts/{draft_id}/mutation-plan/approve",
            json={"plan_id": plan_id},
        )

    assert response.status_code == 200
    execution = response.json()["plan"]["items"][0]["execution_proposed"]
    assert execution["variants"] == [{"sku": "SAFE-SKU"}]
    assert "inventory_quantity" not in execution["variants"][0]


def _sku_pattern_product(*, missing_sku: bool = True):
    return {
        "title": "Pattern Tee",
        "vendor": "Acme",
        "image_src": "https://example.com/tee.jpg",
        "options": [{"name": "Color", "values": ["Black", "White"]}],
        "variants": [
            {
                "option_values": [{"option_name": "Color", "name": "Black"}],
                "sku": "TEE-BLACK",
                "price": "20",
            },
            {
                "option_values": [{"option_name": "Color", "name": "White"}],
                "sku": "" if missing_sku else "TEE-WHITE",
                "price": "20",
            },
        ],
        "source_refs": [
            {"value_path": "title", "cell": "A2"},
            {"value_path": "variants[1].sku", "cell": "D3"},
        ],
    }


@pytest.mark.asyncio
async def test_preflight_persists_generated_sku_once_with_required_derived_authority(
    monkeypatch,
):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "derived-sku-preflight"
    shop_domain = "derived-sku.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [_sku_pattern_product()],
    }

    class FakeShopifyClient:
        async def graphql(self, query, variables=None):
            if query == mutation_plans_api.ACCESS_SCOPES_QUERY:
                return {
                    "data": {
                        "currentAppInstallation": {
                            "accessScopes": [{"handle": "write_products"}]
                        },
                        "shop": {"currencyCode": "USD"},
                    }
                }
            assert query == mutation_plans_api.VARIANTS_BY_SKU_QUERY
            return {"data": {"productVariants": {"nodes": []}}}

        async def close(self):
            return None

    async def fake_offline_client(shop):
        assert shop == shop_domain
        return FakeShopifyClient()

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)
    headers = {"x-shop-domain": shop_domain}
    url = f"/agents/product-drafts/{draft_id}/mutation-plan"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    ) as client:
        first = await client.post(url)
        second = await client.post(url)

    assert first.status_code == second.status_code == 200
    assert first.json()["plan"]["draft_revision"] == 2
    assert second.json()["plan"]["draft_revision"] == 2
    saved = service.product_drafts[draft_id]
    assert saved["revision"] == 2
    assert saved["products"][0]["variants"][1]["sku"] == "TEE-WHITE"
    assert saved["products"][0]["value_origins"]["variants[1].sku"] == "derived"
    assert all(
        ref.get("value_path") != "variants[1].sku"
        for ref in saved["products"][0]["source_refs"]
    )
    derived_row = next(
        row
        for row in first.json()["plan"]["items"][0]["review_changes"]
        if row["field"] == "sku" and row.get("proposed") == "TEE-WHITE"
    )
    assert derived_row["origin"] == "derived"
    assert derived_row["required"] is True
    assert derived_row["selectable"] is False
    assert derived_row["included"] is True
    regenerated_row = next(
        row
        for row in second.json()["plan"]["items"][0]["review_changes"]
        if row["field"] == "sku" and row.get("proposed") == "TEE-WHITE"
    )
    assert regenerated_row["origin"] == "derived"
    first_title = next(
        cell
        for cell in first.json()["plan"]["items"][0]["review_preview"]["product"]["cells"]
        if cell["column_key"] == "title"
    )
    second_title = next(
        cell
        for cell in second.json()["plan"]["items"][0]["review_preview"]["product"]["cells"]
        if cell["column_key"] == "title"
    )
    assert first_title["source_ref"] == second_title["source_ref"] == {
        "value_path": "title",
        "cell": "A2",
        "evidence_role": "value",
    }
    assert all(
        issue["code"] != "SKU_REQUIRED"
        for issue in first.json()["plan"]["items"][0]["quality_issues"]
    )


@pytest.mark.asyncio
async def test_v5_plan_skips_legacy_evidence_repair():
    from types import SimpleNamespace

    from api.agents.mutation_plans import _repair_legacy_review_evidence

    class FailingFiles:
        def get_file(self, _file_id):
            raise AssertionError("v5 regeneration must not rebuild trusted evidence")

    draft = {"draft_id": "draft-1", "input_file_id": "file-1", "products": []}
    previous = {
        "status": "review",
        "review_contract_version": 5,
        "items": [],
    }
    ctx = SimpleNamespace(supabase=SimpleNamespace(file=FailingFiles()))

    assert await _repair_legacy_review_evidence(
        draft, previous, "shop.myshopify.com", ctx
    ) is draft


@pytest.mark.asyncio
@pytest.mark.parametrize("collision_kind", ["import", "live"])
async def test_generated_sku_collisions_remain_blocked_without_suffixes(
    monkeypatch, collision_kind
):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = f"generated-sku-{collision_kind}-collision"
    shop_domain = f"{collision_kind}-collision.myshopify.com"
    products = [_sku_pattern_product()]
    if collision_kind == "import":
        products.append(
            {
                "title": "Other",
                "vendor": "Acme",
                "image_src": "https://example.com/other.jpg",
                "variants": [{"sku": "TEE-WHITE", "price": "10"}],
                "source_refs": [{"value_path": "title", "cell": "A5"}],
            }
        )
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
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
            assert query == mutation_plans_api.VARIANTS_BY_SKU_QUERY
            sku = (
                str((variables or {}).get("query") or "")
                .removeprefix('sku:"')
                .removesuffix('"')
            )
            nodes = (
                [
                    {
                        "id": "gid://shopify/ProductVariant/9",
                        "sku": "TEE-WHITE",
                        "product": {"id": "gid://shopify/Product/9"},
                    }
                ]
                if collision_kind == "live" and sku == "TEE-WHITE"
                else []
            )
            return {"data": {"productVariants": {"nodes": nodes}}}

        async def close(self):
            return None

    async def fake_offline_client(shop):
        return FakeShopifyClient()

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        response = await client.post(f"/agents/product-drafts/{draft_id}/mutation-plan")

    assert response.status_code == 200
    assert response.json()["plan"]["draft_revision"] == 1
    first = response.json()["plan"]["items"][0]
    assert any(issue["code"] == "SKU_REQUIRED" for issue in first["quality_issues"])
    assert first["proposed"]["variants"][1].get("sku") in (None, "")
    assert "TEE-WHITE-2" not in str(first)


@pytest.mark.asyncio
async def test_update_blanks_keep_live_required_values_but_explicit_invalid_price_blocks(
    monkeypatch,
):
    import api.agents.mutation_plans as mutation_plans_api

    draft_id = "live-required-carry-forward"
    shop_domain = "live-required.myshopify.com"
    service = get_app_context().services.supabase._service
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop_domain,
        "revision": 1,
        "products": [
            {
                "title": "",
                "handle": "live-tee",
                "options": [{"name": "Color", "values": ["Black", "White"]}],
                "variants": [
                    {
                        "option_values": [{"option_name": "Color", "name": "Black"}],
                        "sku": "",
                        "price": "",
                    },
                    {
                        "option_values": [{"option_name": "Color", "name": "White"}],
                        "sku": "NEW-WHITE",
                        "price": "not-a-price",
                    },
                ],
            }
        ],
    }
    live = {
        "id": "gid://shopify/Product/1",
        "title": "Live Tee",
        "handle": "live-tee",
        "status": "ACTIVE",
        "tags": [],
        "seo": {},
        "options": [
            {
                "name": "Color",
                "position": 1,
                "optionValues": [{"name": "Black"}, {"name": "White"}],
            }
        ],
        "variants": {
            "nodes": [
                {
                    "id": "gid://shopify/ProductVariant/1",
                    "selectedOptions": [{"name": "Color", "value": "Black"}],
                    "sku": "LIVE-BLACK",
                    "price": "25",
                },
                {
                    "id": "gid://shopify/ProductVariant/2",
                    "selectedOptions": [{"name": "Color", "value": "White"}],
                    "sku": "LIVE-WHITE",
                    "price": "30",
                },
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
        "metafields": {"nodes": []},
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
            if query == mutation_plans_api.PRODUCT_BY_HANDLE_QUERY:
                return {"data": {"products": {"nodes": [live]}}}
            assert query == mutation_plans_api.VARIANTS_BY_SKU_QUERY
            return {"data": {"productVariants": {"nodes": []}}}

        async def close(self):
            return None

    async def fake_offline_client(shop):
        return FakeShopifyClient()

    monkeypatch.setattr(mutation_plans_api, "_offline_client", fake_offline_client)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"x-shop-domain": shop_domain},
    ) as client:
        response = await client.post(f"/agents/product-drafts/{draft_id}/mutation-plan")

    assert response.status_code == 200
    item = response.json()["plan"]["items"][0]
    assert item["proposed"]["title"] == "Live Tee"
    assert item["proposed"]["variants"][0]["sku"] == "LIVE-BLACK"
    assert item["proposed"]["variants"][0]["price"] == "25"
    blocker_paths = {
        issue["value_path"]
        for issue in item["quality_issues"]
        if issue["severity"] == "blocker"
    }
    assert "variants[0].sku" not in blocker_paths
    assert "variants[0].price" not in blocker_paths
    assert "variants[1].price" in blocker_paths
