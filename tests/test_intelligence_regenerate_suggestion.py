import pytest

from app_context import get_app_context
import application.use_cases.intelligence_regenerate_suggestion as regeneration_uc


SHOP = "store.myshopify.com"
SOURCE_ID = "source-suggestion"


def _seed_source(service):
    service.product_intelligence_suggestions[SOURCE_ID] = {
        "suggestion_id": SOURCE_ID,
        "shop_domain": SHOP,
        "status": "pending",
        "product_id": "gid://shopify/Product/1",
        "product_title": "Catalog Product",
        "product_index": 0,
        "audit_id": "audit-1",
        "finding_id": "finding-1",
        "category": "seo_readiness",
        "severity": "medium",
        "message": "Improve product copy",
        "details": {},
        "patch_payload": {
            "seo_title": "Original title proposal",
            "seo_description": "Keep this description",
        },
    }


@pytest.mark.asyncio
async def test_regeneration_through_adapter_supersedes_source_and_keeps_children_pending(
    monkeypatch,
):
    adapter = get_app_context().services.supabase
    service = adapter._service
    _seed_source(service)

    async def fake_generate(**_kwargs):
        return [{"patch_payload": {"seo_title": "Regenerated title"}}]

    monkeypatch.setattr(regeneration_uc, "generate", fake_generate)

    result = await regeneration_uc.execute(
        supabase=adapter,
        suggestion_id=SOURCE_ID,
        field="seo_title",
        product={"id": "gid://shopify/Product/1", "title": "Catalog Product"},
        shop_domain=SHOP,
    )

    assert result["source_suggestion"]["status"] == "superseded"
    assert result["suggestion"]["status"] == "pending"
    assert result["suggestion"]["patch_payload"] == {"seo_title": "Regenerated title"}
    assert result["carry_forward_suggestion"]["status"] == "pending"
    assert result["carry_forward_suggestion"]["patch_payload"] == {
        "seo_description": "Keep this description"
    }


@pytest.mark.asyncio
async def test_regeneration_failure_supersedes_all_created_children(monkeypatch):
    adapter = get_app_context().services.supabase
    service = adapter._service
    _seed_source(service)

    async def fake_generate(**_kwargs):
        return [{"patch_payload": {"seo_title": "Regenerated title"}}]

    monkeypatch.setattr(regeneration_uc, "generate", fake_generate)
    monkeypatch.setattr(
        adapter,
        "mark_product_intelligence_suggestion_superseded",
        lambda *, suggestion_id, shop_domain=None: (
            None
            if suggestion_id == SOURCE_ID
            else service.mark_product_intelligence_suggestion_superseded(
                suggestion_id=suggestion_id, shop_domain=shop_domain
            )
        ),
    )

    with pytest.raises(RuntimeError, match="Failed to supersede source suggestion"):
        await regeneration_uc.execute(
            supabase=adapter,
            suggestion_id=SOURCE_ID,
            field="seo_title",
            product={"id": "gid://shopify/Product/1", "title": "Catalog Product"},
            shop_domain=SHOP,
        )

    children = [
        item
        for item in service.product_intelligence_suggestions.values()
        if item.get("parent_suggestion_id") == SOURCE_ID
    ]
    assert len(children) == 2
    assert {item["status"] for item in children} == {"superseded"}
    assert service.product_intelligence_suggestions[SOURCE_ID]["status"] == "pending"
