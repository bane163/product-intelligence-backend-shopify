import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from main import app


def _force_in_memory_storage(monkeypatch):
    ctx = get_app_context()
    service = getattr(ctx.services.supabase, "_service", None)
    if service is not None:
        monkeypatch.setattr(service, "_try_get_bucket", lambda: None, raising=False)
        monkeypatch.setattr(service, "_get_supabase_client", lambda: None, raising=False)
    monkeypatch.setattr(
        ctx.services.supabase, "_try_get_bucket", lambda: None, raising=False
    )
    monkeypatch.setattr(
        ctx.services.supabase, "_get_supabase_client", lambda: None, raising=False
    )


@pytest.mark.asyncio
async def test_release_gate_intelligence_path_audit_apply_revert_is_tenant_scoped(
    monkeypatch,
):
    _force_in_memory_storage(monkeypatch)
    tenant_header = {"x-shop-domain": "release-gate-path.myshopify.com"}

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
                    "seo": {"title": "Catalog Product", "description": "Catalog description"},
                }
            }
        }

    async def fake_update_product_from_input(self, product):
        return {
            "data": {
                "productUpdate": {
                    "product": {"id": product.get("id")},
                    "userErrors": [],
                }
            }
        }

    async def fake_generate_suggestions_execute(
        *,
        supabase,
        products,
        shop_domain,
        normalization_settings=None,
        trace_event=None,
    ):
        _ = (supabase, shop_domain, normalization_settings, trace_event)
        title = str(products[0].get("title") or "Catalog Product") if products else "Catalog Product"
        return [
            {
                "suggestion_id": f"suggestion-{uuid.uuid4()}",
                "finding_id": f"finding-{uuid.uuid4()}",
                "product_index": 0,
                "product_title": title,
                "category": "completeness",
                "severity": "medium",
                "message": "Add vendor detail",
                "patch_payload": {"vendor": "Improved Vendor"},
                "status": "pending",
            }
        ]

    import application.use_cases.intelligence_generate_suggestions as suggestions_uc
    import shopify as shopify_module

    monkeypatch.setattr(shopify_module.ShopifyClient, "get_product", fake_get_product)
    monkeypatch.setattr(
        shopify_module.ShopifyClient,
        "update_product_from_input",
        fake_update_product_from_input,
    )
    monkeypatch.setattr(suggestions_uc, "execute", fake_generate_suggestions_execute)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        audit_run = await ac.post(
            "/agents/intelligence/audit",
            headers=tenant_header,
            json={
                "products": [
                    {
                        "id": "gid://shopify/Product/9001",
                        "title": "Catalog Product",
                        "handle": "catalog-product",
                    }
                ]
            },
        )
        assert audit_run.status_code == 200
        audit_id = audit_run.json()["audit_id"]

        suggestions = await ac.get(
            f"/agents/intelligence/audits/{audit_id}/suggestions",
            headers=tenant_header,
        )
        assert suggestions.status_code == 200
        suggestion_rows = suggestions.json()["suggestions"]
        assert len(suggestion_rows) == 1

        suggestion_id = suggestion_rows[0]["suggestion_id"]
        apply_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/apply",
            headers=tenant_header,
        )
        assert apply_single.status_code == 200
        assert apply_single.json()["status"] == "applied"
        assert apply_single.json()["shopify_updated"] is True
        assert apply_single.json()["target_product_id"] == "gid://shopify/Product/9001"
        applied_suggestions = await ac.get(
            f"/agents/intelligence/audits/{audit_id}/suggestions",
            headers=tenant_header,
        )
        assert applied_suggestions.status_code == 200
        applied_statuses = {
            row["suggestion_id"]: row.get("status")
            for row in applied_suggestions.json()["suggestions"]
        }
        assert applied_statuses.get(suggestion_id) == "applied"

        revert_single = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/revert",
            headers=tenant_header,
        )
        assert revert_single.status_code == 200
        assert revert_single.json()["status"] == "reverted"
        assert revert_single.json()["shopify_updated"] is True
        assert revert_single.json()["target_product_id"] == "gid://shopify/Product/9001"
        reverted_suggestions = await ac.get(
            f"/agents/intelligence/audits/{audit_id}/suggestions",
            headers=tenant_header,
        )
        assert reverted_suggestions.status_code == 200
        reverted_statuses = {
            row["suggestion_id"]: row.get("status")
            for row in reverted_suggestions.json()["suggestions"]
        }
        assert reverted_statuses.get(suggestion_id) == "reverted"

        cross_tenant = await ac.get(
            f"/agents/intelligence/audits/{audit_id}",
            headers={"x-shop-domain": "release-gate-other.myshopify.com"},
        )
        assert cross_tenant.status_code == 404


@pytest.mark.asyncio
async def test_release_gate_apply_bulk_idempotency_replay_and_conflict(monkeypatch):
    _force_in_memory_storage(monkeypatch)
    applied_calls: list[str] = []

    async def fake_apply_suggestion_execute(
        *,
        supabase,
        shopify,
        suggestion_id,
        patch_payload=None,
        shop_domain=None,
    ):
        _ = (supabase, shopify, patch_payload, shop_domain)
        applied_calls.append(str(suggestion_id))
        return {
            "status": "applied",
            "shopify_updated": True,
            "target_product_id": f"gid://shopify/Product/{suggestion_id}",
        }

    import application.use_cases.intelligence_apply_suggestion as apply_uc

    monkeypatch.setattr(apply_uc, "execute", fake_apply_suggestion_execute)

    idempotency_key = f"release-gate-{uuid.uuid4()}"
    headers = {
        "x-shop-domain": "release-gate-bulk.myshopify.com",
        "Idempotency-Key": idempotency_key,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        first = await ac.post(
            "/agents/intelligence/suggestions/apply-bulk",
            headers=headers,
            json={"suggestion_ids": ["s-1", "s-2"]},
        )
        assert first.status_code == 200
        first_body = first.json()
        assert first_body["applied_count"] == 2
        assert first_body["failed_count"] == 0
        assert first_body["idempotency_key"] == idempotency_key
        assert first_body["idempotency_replayed"] is False
        assert first_body["failed"] == []
        assert [item["suggestion_id"] for item in first_body["results"]] == ["s-1", "s-2"]

        replay = await ac.post(
            "/agents/intelligence/suggestions/apply-bulk",
            headers=headers,
            json={"suggestion_ids": ["s-1", "s-2"]},
        )
        assert replay.status_code == 200
        replay_body = replay.json()
        assert replay_body["applied_count"] == 2
        assert replay_body["failed_count"] == 0
        assert replay_body["idempotency_key"] == idempotency_key
        assert replay_body["idempotency_replayed"] is True
        assert replay_body["results"] == first_body["results"]
        assert replay_body["failed"] == first_body["failed"]

        conflict = await ac.post(
            "/agents/intelligence/suggestions/apply-bulk",
            headers=headers,
            json={"suggestion_ids": ["s-1"]},
        )
        assert conflict.status_code == 409
        conflict_detail = str(conflict.json().get("detail", "")).lower()
        assert "idempotency_key" in conflict_detail
        assert "different payload" in conflict_detail

    assert applied_calls == ["s-1", "s-2"]


@pytest.mark.asyncio
async def test_release_gate_intelligence_endpoints_enforce_tenant_negative_paths(
    monkeypatch,
):
    _force_in_memory_storage(monkeypatch)
    owner_tenant = "release-gate-owner.myshopify.com"
    other_tenant = "release-gate-other.myshopify.com"
    audit_id = f"audit-{uuid.uuid4()}"
    suggestion_id = f"suggestion-{uuid.uuid4()}"

    ctx = get_app_context()
    ctx.services.supabase.intelligence.save_product_intelligence_audit(
        audit_id=audit_id,
        run_id=None,
        submitted_id=None,
        scope="adhoc_products",
        status="success",
        overall_score=70,
        findings_count=1,
        component_scores={},
        totals={
            "audited_products": [
                {
                    "id": "gid://shopify/Product/777",
                    "title": "Catalog Product",
                    "handle": "catalog-product",
                }
            ]
        },
        shop_domain=owner_tenant,
    )
    ctx.services.supabase.intelligence.save_product_intelligence_suggestions(
        audit_id=audit_id,
        suggestions=[
            {
                "suggestion_id": suggestion_id,
                "finding_id": f"finding-{uuid.uuid4()}",
                "product_index": 0,
                "product_title": "Catalog Product",
                "category": "completeness",
                "severity": "medium",
                "message": "Add vendor detail",
                "patch_payload": {"vendor": "Improved Vendor"},
                "status": "pending",
            }
        ],
        shop_domain=owner_tenant,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        audit_missing_tenant = await ac.post(
            "/agents/intelligence/audit",
            json={
                "products": [
                    {"id": "gid://shopify/Product/777", "title": "Catalog Product"}
                ]
            },
        )
        assert audit_missing_tenant.status_code == 400
        assert "shop_domain" in str(audit_missing_tenant.json().get("detail", "")).lower()

        audit_mismatched_tenant = await ac.post(
            "/agents/intelligence/audit",
            headers={"x-shop-domain": owner_tenant},
            json={
                "shop_domain": other_tenant,
                "products": [
                    {"id": "gid://shopify/Product/777", "title": "Catalog Product"}
                ],
            },
        )
        assert audit_mismatched_tenant.status_code == 403

        missing_tenant = await ac.get("/agents/intelligence/audits")
        assert missing_tenant.status_code == 400
        assert "shop_domain" in str(missing_tenant.json().get("detail", "")).lower()

        mismatched_tenant = await ac.get(
            "/agents/intelligence/audits",
            headers={"x-shop-domain": owner_tenant},
            params={"shop_domain": other_tenant},
        )
        assert mismatched_tenant.status_code == 403

        cross_tenant_detail = await ac.get(
            f"/agents/intelligence/audits/{audit_id}",
            headers={"x-shop-domain": other_tenant},
        )
        assert cross_tenant_detail.status_code == 404

        cross_tenant_suggestions = await ac.get(
            f"/agents/intelligence/audits/{audit_id}/suggestions",
            headers={"x-shop-domain": other_tenant},
        )
        assert cross_tenant_suggestions.status_code == 200
        assert cross_tenant_suggestions.json()["suggestions"] == []

        cross_tenant_apply = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/apply",
            headers={"x-shop-domain": other_tenant},
        )
        assert cross_tenant_apply.status_code == 404

        cross_tenant_revert = await ac.post(
            f"/agents/intelligence/suggestions/{suggestion_id}/revert",
            headers={"x-shop-domain": other_tenant},
        )
        assert cross_tenant_revert.status_code == 404

        bulk_missing_tenant = await ac.post(
            "/agents/intelligence/suggestions/apply-bulk",
            json={"suggestion_ids": [suggestion_id]},
        )
        assert bulk_missing_tenant.status_code == 400

        bulk_mismatched_tenant = await ac.post(
            "/agents/intelligence/suggestions/apply-bulk",
            headers={"x-shop-domain": owner_tenant},
            json={"suggestion_ids": [suggestion_id], "shop_domain": other_tenant},
        )
        assert bulk_mismatched_tenant.status_code == 403
