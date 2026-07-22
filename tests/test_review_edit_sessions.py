import io
from datetime import datetime, timedelta, timezone

import openpyxl
import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from main import app


@pytest.mark.asyncio
async def test_grid_edit_session_autosaves_by_change_id_and_resumes():
    service = get_app_context().services.supabase._service
    shop = "review-edits.myshopify.com"
    draft_id = "draft-review-edits"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 3,
        "products": [{"title": "Original", "vendor": "Vendor"}],
    }
    service.mutation_plans["plan-review-edits"] = {
        "plan_id": "plan-review-edits",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "item-1",
            "source_index": 0,
            "review_changes": [{
                "change_id": "title-change",
                "value_path": "title",
                "field": "title",
                "scope": "product",
            }],
        }],
    }
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 3})
        assert created.status_code == 200
        session = created.json()["session"]
        resumed = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 3})
        stale = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={"expected_edit_revision": 9, "changes": []},
        )
        saved = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "changes": [{"change_id": "title-change", "value_path": "title", "value": "Edited"}],
            },
        )
        active = await client.get(base, headers=headers)

    assert resumed.json()["resumed"] is True
    assert resumed.json()["session"]["session_id"] == session["session_id"]
    assert stale.status_code == 409
    assert saved.status_code == 200
    assert saved.json()["session"]["edit_revision"] == 2
    assert saved.json()["session"]["pending_products"][0]["title"] == "Edited"
    assert saved.json()["session"]["pending_products"][0]["_value_origins"]["title"] == "merchant_edited"
    assert active.json()["session"]["session_id"] == session["session_id"]


@pytest.mark.asyncio
async def test_edit_sessions_are_tenant_scoped_and_enforce_one_surface():
    service = get_app_context().services.supabase._service
    shop = "tenant-a.myshopify.com"
    draft_id = "tenant-edit-draft"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [{"title": "Product"}],
    }
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers={"x-shop-domain": shop}, json={"surface": "grid", "base_revision": 1})
        switched = await client.post(base, headers={"x-shop-domain": shop}, json={"surface": "workbook", "base_revision": 1})
        cross_tenant = await client.delete(
            f"{base}/{created.json()['session']['session_id']}",
            headers={"x-shop-domain": "tenant-b.myshopify.com"},
        )
        discarded = await client.delete(
            f"{base}/{created.json()['session']['session_id']}",
            headers={"x-shop-domain": shop},
        )

    assert switched.status_code == 409
    assert cross_tenant.status_code == 404
    assert discarded.status_code == 200
    assert discarded.json()["session"]["status"] == "discarded"


@pytest.mark.asyncio
async def test_workbook_apply_parses_saved_copy_and_rebuilds_once(monkeypatch):
    import api.agents.review_edit_sessions as sessions_api

    service = get_app_context().services.supabase._service
    shop = "workbook-edits.myshopify.com"
    draft_id = "workbook-edit-draft"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "output_file_id": "canonical-workbook",
        "output_filename": "generated-import.xlsx",
        "products": [{
            "handle": "linen-shirt",
            "title": "Linen shirt",
            "options": [{"name": "Size", "values": ["S"]}],
            "variants": [{"option1": "S", "sku": "LS-S", "price": 29}],
        }],
    }

    async def fake_create_plan(draft_id, request, ctx):
        saved = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=shop)
        assert saved["revision"] == 2
        assert saved["products"][0]["variants"][0]["price"] == 35.0
        return {"plan": {"plan_id": "rebuilt-once", "draft_revision": 2}}

    monkeypatch.setattr(sessions_api, "create_mutation_plan", fake_create_plan)
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    headers = {"x-shop-domain": shop}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "workbook", "base_revision": 1})
        session = created.json()["session"]
        working = service.get_file(session["working_file_id"])
        workbook = openpyxl.load_workbook(io.BytesIO(working["content"]))
        sheet = workbook["Products"]
        price_column = next(cell.column for cell in sheet[1] if cell.value == "Variant Price")
        sheet.cell(2, price_column).value = 35
        output = io.BytesIO()
        workbook.save(output)
        service.save_file(
            session["working_file_id"],
            working["name"],
            output.getvalue(),
            working["content_type"],
            file_origin="review_working_copy",
            shop_domain=shop,
        )
        applied = await client.post(
            f"{base}/{session['session_id']}/apply",
            headers=headers,
            json={"expected_edit_revision": 1},
        )

    assert applied.status_code == 200, applied.text
    assert applied.json()["session"]["status"] == "applied"
    assert applied.json()["plan"]["plan_id"] == "rebuilt-once"
    assert isinstance(applied.json()["workbook_revision"], str)
    assert len(applied.json()["workbook_revision"]) == 64
    assert service.product_drafts[draft_id]["products"][0]["value_origins"]["variant[size-s].price"] == "merchant_edited"
    assert service.get_file("canonical-workbook")["content_revision"]
    assert service.get_file(session["working_file_id"]) is None


def test_expired_workbook_sessions_are_cleaned_up_with_their_working_copy():
    service = get_app_context().services.supabase._service
    shop = "expired-edits.myshopify.com"
    file_id = "expired-working-copy"
    service.save_file(file_id, "working.xlsx", b"bytes", "application/octet-stream", file_origin="review_working_copy", shop_domain=shop)
    service.save_review_edit_session({
        "session_id": "expired-session",
        "draft_id": "expired-draft",
        "shop_domain": shop,
        "surface": "workbook",
        "base_draft_revision": 1,
        "edit_revision": 1,
        "pending_products": [],
        "working_file_id": file_id,
        "status": "active",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    })

    assert service.get_active_review_edit_session("expired-draft", shop_domain=shop) is None
    assert service.review_edit_sessions["expired-session"]["status"] == "expired"
    assert service.get_file(file_id) is None
