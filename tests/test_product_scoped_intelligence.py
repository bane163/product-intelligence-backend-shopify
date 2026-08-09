import json

import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from application.use_cases.intelligence_get_product_overview import execute as overview
from application.use_cases.intelligence_run_audit import (
    SCORING_VERSION,
    _build_product_results,
    _score_products,
)
from infrastructure.adapters.supabase_namespaces import SupabaseDomainAccessors
from services.supabase_service import SupabaseService
from main import app


def product(product_id: str, title: str, **overrides):
    return {
        "id": product_id,
        "title": title,
        "vendor": "Vendor",
        "product_type": "General",
        "body_html": "Detailed description\n- Feature one\n- Feature two" * 5,
        "seo_title": title,
        "seo_description": "A complete search description for the product.",
        "tags": ["general"],
        "status": "active",
        "product_category": "General",
        **overrides,
    }


def seed_audit(service, audit_id: str, shop_domain: str, status: str = "success"):
    service.product_intelligence_audits[audit_id] = {
        "audit_id": audit_id,
        "shop_domain": shop_domain,
        "status": status,
    }


def test_scores_products_independently_and_catalog_uses_rounded_means():
    healthy = product("gid://shopify/Product/1", "A complete healthy product title")
    incomplete = product(
        "gid://shopify/Product/2", "Short", vendor="", product_type="",
        body_html="", seo_title="", seo_description="", tags=[], product_category="",
    )
    results = _build_product_results([healthy, incomplete])
    means, findings = _score_products([healthy, incomplete])

    assert results[0]["score"] > results[1]["score"]
    assert all(row["scoring_version"] == SCORING_VERSION for row in results)
    assert means["completeness"] == round(
        (results[0]["component_scores"]["completeness"] + results[1]["component_scores"]["completeness"]) / 2
    )
    assert {row["product_id"] for row in findings} == {"gid://shopify/Product/2"}


def test_latest_product_result_is_tenant_scoped_and_legacy_is_not_synthesized():
    service = SupabaseService()
    service._get_supabase_client = lambda: None
    supabase = SupabaseDomainAccessors(service)
    shop = "one.myshopify.com"
    other_shop = "two.myshopify.com"
    product_id = "gid://shopify/Product/1"
    seed_audit(service, "audit-old", shop)
    seed_audit(service, "audit-new", shop)
    seed_audit(service, "audit-other", other_shop)
    service.save_product_intelligence_product_results(
        audit_id="audit-old", shop_domain=shop,
        results=[{"product_id": product_id, "score": 60, "component_scores": {}, "findings_count": 2,
                  "audited_snapshot": {}, "scoring_version": SCORING_VERSION, "created_at": "2026-01-01T00:00:00Z"}],
    )
    service.save_product_intelligence_product_results(
        audit_id="audit-new", shop_domain=shop,
        results=[{"product_id": product_id, "score": 88, "component_scores": {"completeness": 90}, "findings_count": 1,
                  "audited_snapshot": {}, "scoring_version": SCORING_VERSION, "created_at": "2026-02-01T00:00:00Z"}],
    )
    service.save_product_intelligence_product_results(
        audit_id="audit-other", shop_domain=other_shop,
        results=[{"product_id": product_id, "score": 10, "component_scores": {}, "findings_count": 9,
                  "audited_snapshot": {}, "scoring_version": SCORING_VERSION, "created_at": "2026-03-01T00:00:00Z"}],
    )

    [result] = overview(supabase=supabase, product_ids=[product_id], shop_domain=shop)
    assert result["health"]["score"] == 88
    assert result["health"]["requires_reaudit"] is False
    assert result["product_title"] == ""

    [legacy] = overview(
        supabase=supabase, product_ids=["gid://shopify/Product/999"], shop_domain=shop,
    )
    assert legacy["health"]["score"] is None
    assert legacy["health"]["requires_reaudit"] is True


def test_overview_marks_latest_applied_or_reverted_audit_stale_until_a_new_audit():
    service = SupabaseService()
    service._get_supabase_client = lambda: None
    supabase = SupabaseDomainAccessors(service)
    shop = "stale.myshopify.com"
    product_id = "gid://shopify/Product/77"
    seed_audit(service, "audit-one", shop)
    service.save_product_intelligence_product_results(
        audit_id="audit-one", shop_domain=shop,
        results=[{"product_id": product_id, "product_title": "Stale Product", "score": 72,
                  "component_scores": {}, "findings_count": 1, "audited_snapshot": {},
                  "scoring_version": SCORING_VERSION, "created_at": "2026-01-01T00:00:00Z"}],
    )
    service.create_product_intelligence_suggestion(
        suggestion={"suggestion_id": "applied-one", "audit_id": "audit-one",
                    "product_id": product_id, "patch_payload": {"vendor": "New"},
                    "status": "applied", "applied_at": "2026-01-02T00:00:00Z"},
        shop_domain=shop,
    )

    [stale] = overview(supabase=supabase, product_ids=[product_id], shop_domain=shop)
    assert stale["product_title"] == "Stale Product"
    assert stale["health"]["score"] == 72
    assert stale["health"]["requires_reaudit"] is True

    service.save_product_intelligence_product_results(
        audit_id="audit-two", shop_domain=shop,
        results=[{"product_id": product_id, "product_title": "Fresh Product", "score": 91,
                  "component_scores": {}, "findings_count": 0, "audited_snapshot": {},
                  "scoring_version": SCORING_VERSION, "created_at": "2026-02-01T00:00:00Z"}],
    )
    seed_audit(service, "audit-two", shop)
    [fresh] = overview(supabase=supabase, product_ids=[product_id], shop_domain=shop)
    assert fresh["product_title"] == "Fresh Product"
    assert fresh["health"]["score"] == 91
    assert fresh["health"]["requires_reaudit"] is False


def test_overview_ignores_results_and_suggestions_from_failed_audits():
    service = SupabaseService()
    service._get_supabase_client = lambda: None
    supabase = SupabaseDomainAccessors(service)
    shop = "failed-audit.myshopify.com"
    product_id = "gid://shopify/Product/88"
    seed_audit(service, "audit-good", shop)
    seed_audit(service, "audit-failed", shop, status="error")
    service.save_product_intelligence_product_results(
        audit_id="audit-good", shop_domain=shop,
        results=[{"product_id": product_id, "score": 80, "component_scores": {},
                  "findings_count": 0, "created_at": "2026-01-01T00:00:00Z"}],
    )
    service.save_product_intelligence_product_results(
        audit_id="audit-failed", shop_domain=shop,
        results=[{"product_id": product_id, "score": 10, "component_scores": {},
                  "findings_count": 9, "created_at": "2026-02-01T00:00:00Z"}],
    )
    service.create_product_intelligence_suggestion(
        suggestion={"suggestion_id": "failed-fix", "audit_id": "audit-failed",
                    "product_id": product_id, "patch_payload": {"title": "Unsafe"},
                    "status": "pending"}, shop_domain=shop,
    )

    [result] = overview(supabase=supabase, product_ids=[product_id], shop_domain=shop)
    assert result["health"]["score"] == 80
    assert result["counts"]["pending_suggestions"] == 0


@pytest.mark.asyncio
async def test_overview_endpoint_enforces_batch_review_and_tenant_boundaries(monkeypatch):
    ctx = get_app_context()
    service = getattr(ctx.services.supabase, "_service", None)
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    shop = "overview-one.myshopify.com"
    product_id = "gid://shopify/Product/7101"
    seed_audit(service, "audit-overview", shop)
    service.save_product_intelligence_product_results(
        audit_id="audit-overview", shop_domain=shop,
        results=[{"product_id": product_id, "score": 81,
                  "component_scores": {"completeness": 82}, "findings_count": 1,
                  "audited_snapshot": {}, "scoring_version": SCORING_VERSION,
                  "created_at": "2026-07-19T00:00:00Z"}],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/intelligence/products/overview", headers={"x-shop-domain": shop},
            json={"product_ids": [product_id, "gid://shopify/Product/7102"], "view": "summary"},
        )
        assert response.status_code == 200
        rows = response.json()["products"]
        assert rows[0]["health"]["score"] == 81
        assert rows[1]["health"]["score"] is None

        other_tenant = await client.post(
            "/agents/intelligence/products/overview",
            headers={"x-shop-domain": "overview-two.myshopify.com"},
            json={"product_ids": [product_id], "view": "summary"},
        )
        assert other_tenant.json()["products"][0]["health"]["requires_reaudit"] is True

        invalid_review = await client.post(
            "/agents/intelligence/products/overview", headers={"x-shop-domain": shop},
            json={"product_ids": [product_id, "gid://shopify/Product/7102"], "view": "review"},
        )
        assert invalid_review.status_code == 400
        oversized = await client.post(
            "/agents/intelligence/products/overview", headers={"x-shop-domain": shop},
            json={"product_ids": [f"gid://shopify/Product/{index}" for index in range(1, 102)]},
        )
        assert oversized.status_code == 400


@pytest.mark.asyncio
async def test_audit_product_ids_fetches_exact_products_server_side(monkeypatch):
    ctx = get_app_context()
    service = getattr(ctx.services.supabase, "_service", None)
    monkeypatch.setattr(service, "_get_supabase_client", lambda: None)
    requested: list[list[str]] = []
    product_id = "gid://shopify/Product/7201"

    async def exact_products(product_ids):
        requested.append(list(product_ids))
        return [product(product_id, "Exact Shopify Product")]

    async def no_suggestions(**_kwargs):
        return []

    monkeypatch.setattr(ctx.services.shopify, "list_products_by_ids", exact_products)
    import application.use_cases.intelligence_generate_suggestions as suggestions_uc
    monkeypatch.setattr(suggestions_uc, "execute", no_suggestions)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/agents/intelligence/audit",
            headers={"x-shop-domain": "exact-products.myshopify.com"},
            data={
                "product_ids_json": json.dumps([product_id]),
                "products_json": json.dumps([product("gid://shopify/Product/9999", "Spoofed")]),
            },
        )
    assert response.status_code == 200
    assert requested == [[product_id]]
    assert response.json()["totals"]["audited_products"][0]["id"] == product_id
