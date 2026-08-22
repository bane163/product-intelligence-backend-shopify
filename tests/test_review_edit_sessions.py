import io

import openpyxl
import pytest
from fastapi import HTTPException
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
    assert saved.json()["session"]["history_cursor"] == 1
    assert saved.json()["session"]["history"][0]["operation"] == "value_edit"
    assert active.json()["session"]["session_id"] == session["session_id"]


@pytest.mark.asyncio
async def test_value_edit_revert_restores_provenance_and_new_edit_truncates_redo():
    service = get_app_context().services.supabase._service
    shop = "durable-value-operations.myshopify.com"
    draft_id = "durable-value-operations-draft"
    source_ref = {
        "file_id": "source-file",
        "sheet": "Products",
        "cell": "A2",
        "value_path": "title",
    }
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [{
            "title": "Imported title",
            "value_origins": {"title": "source"},
            "source_refs": [source_ref],
        }],
    }
    service.mutation_plans["durable-value-operations-plan"] = {
        "plan_id": "durable-value-operations-plan",
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
                "proposed": "Imported title",
                "origin": "source",
                "source_ref": source_ref,
            }],
        }],
    }
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        assert created.status_code == 200, created.text
        session = created.json()["session"]
        original_row = session["working_plan"]["items"][0]["review_changes"][0]
        assert original_row["original_proposed"] == "Imported title"
        assert original_row["original_origin"] == "source"
        assert original_row["original_source_ref"] == source_ref

        edited = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operation": {
                    "type": "value_edit",
                    "change_id": "title-change",
                    "value_path": "title",
                    "value": "Merchant title",
                },
            },
        )
        assert edited.status_code == 200, edited.text
        edited_session = edited.json()["session"]
        edited_row = edited_session["working_plan"]["items"][0]["review_changes"][0]
        assert edited_session["pending_products"][0]["title"] == "Merchant title"
        assert edited_session["pending_products"][0]["value_origins"]["title"] == "merchant_edited"
        assert edited_row["proposed"] == "Merchant title"
        assert edited_row["origin"] == "merchant_edited"
        assert "source_ref" not in edited_row

        reverted = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 2,
                "operation": "value_revert",
                "change_id": "title-change",
                "value_path": "title",
            },
        )
        assert reverted.status_code == 200, reverted.text
        reverted_session = reverted.json()["session"]
        reverted_row = reverted_session["working_plan"]["items"][0]["review_changes"][0]
        assert reverted_session["pending_products"][0]["title"] == "Imported title"
        assert reverted_session["pending_products"][0]["value_origins"]["title"] == "source"
        assert reverted_row["proposed"] == "Imported title"
        assert reverted_row["origin"] == "source"
        assert reverted_row["source_ref"] == source_ref
        assert reverted_session["history_cursor"] == 2
        assert [entry["operation"] for entry in reverted_session["history"]] == [
            "value_edit",
            "value_revert",
        ]

        undo_revert = await client.post(
            f"{base}/{session['session_id']}/undo",
            headers=headers,
            json={"expected_edit_revision": 3},
        )
        redo_revert = await client.post(
            f"{base}/{session['session_id']}/redo",
            headers=headers,
            json={"expected_edit_revision": 4},
        )
        undo_before_branch = await client.post(
            f"{base}/{session['session_id']}/undo",
            headers=headers,
            json={"expected_edit_revision": 5},
        )
        branched = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 6,
                "operations": [{
                    "operation": "value_edit",
                    "change_id": "title-change",
                    "value_path": "title",
                    "value": "Branched title",
                }],
            },
        )

    assert undo_revert.status_code == 200, undo_revert.text
    undo_revert_session = undo_revert.json()["session"]
    assert undo_revert_session["pending_products"][0]["title"] == "Merchant title"
    assert undo_revert_session["pending_products"][0]["value_origins"]["title"] == "merchant_edited"
    assert undo_revert_session["working_plan"]["items"][0]["review_changes"][0]["origin"] == "merchant_edited"
    assert redo_revert.status_code == 200, redo_revert.text
    assert redo_revert.json()["session"]["pending_products"][0]["title"] == "Imported title"
    assert redo_revert.json()["session"]["working_plan"]["items"][0]["review_changes"][0]["source_ref"] == source_ref
    assert undo_before_branch.status_code == 200, undo_before_branch.text
    assert branched.status_code == 200, branched.text
    branched_session = branched.json()["session"]
    assert branched_session["history_cursor"] == 2
    assert len(branched_session["history"]) == 2
    assert branched_session["history"][1]["operation"] == "value_edit"
    assert branched_session["history"][1]["after"]["proposed"] == "Branched title"


def _seed_durable_history_review(service, shop: str, draft_id: str) -> None:
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [{
            "title": "Base title",
            "vendor": "Base vendor",
            "value_origins": {"title": "source", "vendor": "source"},
        }],
    }
    service.mutation_plans[f"{draft_id}-plan"] = {
        "plan_id": f"{draft_id}-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "item-1",
            "source_index": 0,
            "selectable": True,
            "selected": True,
            "review_changes": [
                {
                    "change_id": "title-change",
                    "value_path": "title",
                    "field": "title",
                    "scope": "product",
                    "proposed": "Base title",
                    "origin": "source",
                    "included": True,
                },
                {
                    "change_id": "vendor-change",
                    "value_path": "vendor",
                    "field": "vendor",
                    "scope": "product",
                    "proposed": "Base vendor",
                    "origin": "source",
                    "included": True,
                },
            ],
        }],
    }


@pytest.mark.asyncio
async def test_durable_ui_operations_survive_reload_and_undo_redo_selection():
    service = get_app_context().services.supabase._service
    shop = "durable-ui-state.myshopify.com"
    draft_id = "durable-ui-state-draft"
    _seed_durable_history_review(service, shop, draft_id)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session = created.json()["session"]
        patched = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operations": [
                    {"type": "filters", "filterValues": {"changes": "all", "query": "shirt"}},
                    {"type": "expansion", "expandedItemIds": ["product:0"]},
                    {"type": "pagination", "pagesByItem": {"item-1": 3}},
                    {"type": "workspace_view", "workspaceView": "changes"},
                    {
                        "type": "selection",
                        "item_id": "item-1",
                        "selected": False,
                        "included_change_ids": [],
                    },
                ],
            },
        )
        resumed = await client.get(base, headers=headers)
        undone = await client.post(
            f"{base}/{session['session_id']}/undo",
            headers=headers,
            json={"expected_edit_revision": 2},
        )
        redone = await client.post(
            f"{base}/{session['session_id']}/redo",
            headers=headers,
            json={"expected_edit_revision": 3},
        )

    assert patched.status_code == 200, patched.text
    patched_session = patched.json()["session"]
    assert patched_session["grid_state"] == {
        "filterValues": {"productStatus": "all"},
        "expandedItemIds": ["product:0"],
        "expansionInitialized": True,
        "pagesByItem": {},
        "productPage": 0,
    }
    assert [entry["operation"] for entry in patched_session["history"]] == ["selection"]
    assert all(entry["operation_id"] for entry in patched_session["history"])
    assert patched_session["history_cursor"] == 1
    assert resumed.json()["session"]["history_cursor"] == 1
    assert undone.status_code == 200, undone.text
    base_session = undone.json()["session"]
    base_item = base_session["working_plan"]["items"][0]
    assert base_session["history_cursor"] == 0
    assert base_session["grid_state"] == {
        "filterValues": {"productStatus": "all"},
        "expandedItemIds": ["product:0"],
        "expansionInitialized": True,
        "pagesByItem": {},
        "productPage": 0,
    }
    assert base_item["selected"] is True
    assert [row["included"] for row in base_item["review_changes"]] == [True, True]
    assert redone.status_code == 200, redone.text
    redone_session = redone.json()["session"]
    redone_item = redone_session["working_plan"]["items"][0]
    assert redone_session["history_cursor"] == 1
    assert redone_session["grid_state"] == patched_session["grid_state"]
    assert redone_item["selected"] is False
    assert [row["included"] for row in redone_item["review_changes"]] == [False, False]


@pytest.mark.asyncio
async def test_workbook_session_preserves_initial_grid_state_and_accepts_ui_operations_only():
    service = get_app_context().services.supabase._service
    shop = "workbook-ui-state.myshopify.com"
    draft_id = "workbook-ui-state-draft"
    _seed_durable_history_review(service, shop, draft_id)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    initial_grid_state = {
        "filterValues": {"changes": "all"},
        "expandedItemIds": ["item-1"],
        "pagesByItem": {"item-1": 2},
        "workspaceView": "spreadsheet",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base,
            headers=headers,
            json={
                "surface": "workbook",
                "base_revision": 1,
                "grid_state": initial_grid_state,
            },
        )
        assert created.status_code == 410
        assert created.json()["detail"]["code"] == "WORKBOOK_REVIEW_RETIRED"
        return
        session = created.json()["session"]
        patched = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operations": [
                    {"type": "expansion", "expandedItemIds": []},
                    {"type": "workspace_view", "workspaceView": "changes"},
                ],
            },
        )
        rejected = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 2,
                "operation": {
                    "type": "value_edit",
                    "change_id": "title-change",
                    "value_path": "title",
                    "value": "Not allowed",
                },
            },
        )
        resumed = await client.get(base, headers=headers)

    assert created.status_code == 200, created.text
    assert session["grid_state"] == {
        "filterValues": {"changes": "all"},
        "expandedItemIds": ["item-1"],
        "pagesByItem": {"item-1": 2},
    }
    assert patched.status_code == 200, patched.text
    assert patched.json()["session"]["grid_state"] == {
        "filterValues": {"changes": "all"},
        "pagesByItem": {"item-1": 2},
        "expandedItemIds": [],
        "expansionInitialized": False,
    }
    assert rejected.status_code == 409
    assert resumed.json()["session"]["grid_state"] == patched.json()["session"]["grid_state"]


@pytest.mark.asyncio
async def test_grid_selection_matches_mutation_plan_required_and_deferred_rules():
    service = get_app_context().services.supabase._service
    shop = "grid-selection-contract.myshopify.com"
    draft_id = "grid-selection-contract-draft"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [{"title": "Review product"}],
    }
    service.mutation_plans["grid-selection-contract-plan"] = {
        "plan_id": "grid-selection-contract-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "server-item-id",
            "source_index": 0,
            "selectable": True,
            "selected": True,
            "review_changes": [
                {"change_id": "required-title", "disposition": "applicable", "required": True, "included": True},
                {"change_id": "optional-vendor", "disposition": "applicable", "required": False, "included": True},
                {"change_id": "deferred-sku", "disposition": "deferred", "required": False, "included": False},
            ],
        }],
    }
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session = created.json()["session"]
        url = f"{base}/{session['session_id']}"
        missing_required = await client.patch(url, headers=headers, json={
            "expected_edit_revision": 1,
            "operation": {"type": "selection", "item_id": "server-item-id", "selected": True, "included_change_ids": ["optional-vendor"]},
        })
        includes_deferred = await client.patch(url, headers=headers, json={
            "expected_edit_revision": 1,
            "operation": {"type": "selection", "item_id": "server-item-id", "selected": True, "included_change_ids": ["required-title", "deferred-sku"]},
        })
        accepted = await client.patch(url, headers=headers, json={
            "expected_edit_revision": 1,
            "operation": {"type": "selection", "item_id": "server-item-id", "selected": True, "included_change_ids": ["required-title"]},
        })

    assert missing_required.status_code == 422
    assert includes_deferred.status_code == 422
    assert accepted.status_code == 200
    rows = accepted.json()["session"]["working_plan"]["items"][0]["review_changes"]
    assert {row["change_id"]: row["included"] for row in rows} == {
        "required-title": True,
        "optional-vendor": False,
        "deferred-sku": False,
    }


@pytest.mark.asyncio
async def test_undo_redo_replays_value_and_new_operation_truncates_redo():
    service = get_app_context().services.supabase._service
    shop = "value-history-actions.myshopify.com"
    draft_id = "value-history-actions-draft"
    _seed_durable_history_review(service, shop, draft_id)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session_id = created.json()["session"]["session_id"]
        edited = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operation": {
                    "type": "value_edit",
                    "change_id": "title-change",
                    "value_path": "title",
                    "value": "Edited title",
                },
            },
        )
        undone = await client.post(
            f"{base}/{session_id}/undo",
            headers=headers,
            json={"expected_edit_revision": 2},
        )
        reloaded = await client.get(base, headers=headers)
        redone = await client.post(
            f"{base}/{session_id}/redo",
            headers=headers,
            json={"expected_edit_revision": 3},
        )
        undo_again = await client.post(
            f"{base}/{session_id}/undo",
            headers=headers,
            json={"expected_edit_revision": 4},
        )
        branched = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={
                "expected_edit_revision": 5,
                "operation": {"type": "workspace_view", "workspaceView": "spreadsheet"},
            },
        )

    assert edited.status_code == 200, edited.text
    assert undone.json()["session"]["pending_products"][0]["title"] == "Base title"
    assert undone.json()["session"]["pending_products"][0]["value_origins"]["title"] == "source"
    undone_row = undone.json()["session"]["working_plan"]["items"][0]["review_changes"][0]
    assert undone_row["proposed"] == "Base title"
    assert undone_row["origin"] == "source"
    assert reloaded.json()["session"]["history_cursor"] == 0
    assert redone.json()["session"]["pending_products"][0]["title"] == "Edited title"
    assert redone.json()["session"]["history_cursor"] == 1
    assert undo_again.status_code == 200, undo_again.text
    branched_session = branched.json()["session"]
    assert branched.status_code == 200, branched.text
    assert branched_session["history_cursor"] == 0
    assert len(branched_session["history"]) == 1
    assert branched_session["history"][0]["operation"] == "value_edit"


@pytest.mark.asyncio
async def test_reset_restores_base_plan_products_and_grid_from_partial_history():
    service = get_app_context().services.supabase._service
    shop = "reset-history.myshopify.com"
    draft_id = "reset-history-draft"
    _seed_durable_history_review(service, shop, draft_id)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session_id = created.json()["session"]["session_id"]
        changed = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operations": [
                    {
                        "type": "value_edit",
                        "change_id": "vendor-change",
                        "value_path": "vendor",
                        "value": "Changed vendor",
                    },
                    {
                        "type": "selection",
                        "item_id": "item-1",
                        "selected": True,
                        "included_change_ids": ["title-change"],
                    },
                    {"type": "filters", "filterValues": {}},
                    {"type": "workspace_view", "workspaceView": "spreadsheet"},
                ],
            },
        )
        undone = await client.post(
            f"{base}/{session_id}/undo",
            headers=headers,
            json={"expected_edit_revision": 2},
        )
        reset = await client.post(
            f"{base}/{session_id}/reset",
            headers=headers,
            json={"expected_edit_revision": 3},
        )
        redo_after_reset = await client.post(
            f"{base}/{session_id}/redo",
            headers=headers,
            json={"expected_edit_revision": 4},
        )

    assert changed.status_code == 200, changed.text
    assert undone.status_code == 200, undone.text
    assert reset.status_code == 200, reset.text
    reset_session = reset.json()["session"]
    assert reset_session["pending_products"][0]["vendor"] == "Base vendor"
    assert reset_session["pending_products"][0]["value_origins"]["vendor"] == "source"
    reset_item = reset_session["working_plan"]["items"][0]
    assert reset_item["selected"] is True
    assert [row["included"] for row in reset_item["review_changes"]] == [True, True]
    assert reset_session["grid_state"] == {
        "filterValues": {"productStatus": "all"},
        "expandedItemIds": [],
        "expansionInitialized": False,
        "pagesByItem": {},
        "productPage": 0,
    }
    assert reset_session["history"] == []
    assert reset_session["history_cursor"] == 0
    assert redo_after_reset.status_code == 409


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

    assert switched.status_code == 410
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

    async def fake_create_plan(draft_id, request, payload, ctx):
        saved = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=shop)
        assert saved["revision"] == 2
        assert saved["products"][0]["variants"][0]["price"] == 35.0
        return {"plan": {"plan_id": "rebuilt-once", "draft_revision": 2}}

    monkeypatch.setattr(sessions_api, "create_mutation_plan", fake_create_plan)
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    headers = {"x-shop-domain": shop}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "workbook", "base_revision": 1})
        assert created.status_code == 410
        assert created.json()["detail"]["code"] == "WORKBOOK_REVIEW_RETIRED"
        return
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


@pytest.mark.asyncio
async def test_missing_required_sku_and_price_can_be_edited_and_applied(monkeypatch):
    import api.agents.review_edit_sessions as sessions_api
    from application.domain.shopify_mutation_plan import (
        build_review_plan_item,
        merge_structured_product,
    )

    service = get_app_context().services.supabase._service
    shop = "required-commerce-edits.myshopify.com"
    draft_id = "required-commerce-edits-draft"
    imported = {
        "title": "Tee",
        "options": [{"name": "Title", "values": ["Default Title"]}],
        "variants": [{"option1": "Default Title"}],
    }
    proposed, _, _ = merge_structured_product(None, imported, {"title": "Tee"})
    proposed["value_origins"] = {}
    blockers = [
        {"code": "SKU_REQUIRED", "field": "variants[0].sku"},
        {"code": "PRICE_REQUIRED", "field": "variants[0].price"},
    ]
    item = build_review_plan_item(
        item_id="required-item",
        currency_code="USD",
        source_index=0,
        source_product=imported,
        live_snapshot=None,
        proposed=proposed,
        operation="create",
        blockers=blockers,
    )
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [proposed],
    }
    service.mutation_plans["required-commerce-edits-plan"] = {
        "plan_id": "required-commerce-edits-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "draft_revision": 1,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "currency_code": "USD",
        "items": [item],
    }
    required_rows = {
        row["field"]: row
        for row in item["review_changes"]
        if row["field"] in {"sku", "price"}
    }

    async def fake_create_plan(draft_id, request, payload, ctx):
        saved = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=shop)
        variant = saved["products"][0]["variants"][0]
        assert variant["sku"] == "TEE-DEFAULT"
        assert variant["price"] == 24.5
        origins = saved["products"][0]["value_origins"]
        assert origins[required_rows["sku"]["value_path"]] == "merchant_edited"
        assert origins[required_rows["price"]["value_path"]] == "merchant_edited"
        return {"plan": {
            "plan_id": "required-commerce-resolved-plan",
            "draft_id": draft_id,
            "shop_domain": shop,
            "draft_revision": 2,
            "version": 2,
            "status": "review",
            "review_contract_version": 2,
            "currency_code": "USD",
            "items": [{
                "item_id": "resolved-item",
                "source_index": 0,
                "operation": "create",
                "blockers": [],
                "selectable": True,
                "selected": True,
                "review_changes": [],
            }],
        }}

    monkeypatch.setattr(sessions_api, "create_mutation_plan", fake_create_plan)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        assert created.status_code == 200, created.text
        session_id = created.json()["session"]["session_id"]
        blank_price = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operation": {
                    "type": "value_edit",
                    "change_id": required_rows["price"]["change_id"],
                    "value_path": required_rows["price"]["value_path"],
                    "value": None,
                },
            },
        )
        edited = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operations": [
                    {
                        "type": "value_edit",
                        "change_id": required_rows["sku"]["change_id"],
                        "value_path": required_rows["sku"]["value_path"],
                        "value": "TEE-DEFAULT",
                    },
                    {
                        "type": "value_edit",
                        "change_id": required_rows["price"]["change_id"],
                        "value_path": required_rows["price"]["value_path"],
                        "value": 24.5,
                    },
                ],
            },
        )
        applied = await client.post(
            f"{base}/{session_id}/apply",
            headers=headers,
            json={"expected_edit_revision": edited.json()["session"]["edit_revision"]},
        )

    assert blank_price.status_code == 422
    blank_error = blank_price.json()["detail"]["field_errors"][0]
    assert blank_error == {
        "item_id": "required-item",
        "change_id": required_rows["price"]["change_id"],
        "cell_id": "",
        "value_path": required_rows["price"]["value_path"],
        "code": "INVALID_REVIEW_VALUE",
        "message": "This field is required.",
        "remedy": "edit",
    }
    assert edited.status_code == 200, edited.text
    assert applied.status_code == 200, applied.text
    rebuilt_item = applied.json()["plan"]["items"][0]
    assert rebuilt_item["blockers"] == []
    assert rebuilt_item["selectable"] is True


@pytest.mark.asyncio
async def test_grid_apply_updates_draft_without_touching_file_storage_and_preserves_selection(monkeypatch):
    import api.agents.review_edit_sessions as sessions_api

    service = get_app_context().services.supabase._service
    shop = "grid-apply.myshopify.com"
    draft_id = "grid-apply-draft"
    canonical_id = "grid-canonical-workbook"
    service.save_file(
        canonical_id,
        "generated-import.xlsx",
        b"canonical-before-grid-apply",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_origin="workflow_output",
        shop_domain=shop,
    )
    original_revision = service.get_file(canonical_id)["content_revision"]
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "output_file_id": canonical_id,
        "output_filename": "generated-import.xlsx",
        "products": [{"title": "Original", "vendor": "Original vendor"}],
    }
    service.mutation_plans["grid-apply-plan"] = {
        "plan_id": "grid-apply-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "working-item",
            "source_index": 0,
            "selectable": True,
            "selected": True,
            "review_changes": [
                {
                    "change_id": "title-change",
                    "value_path": "title",
                    "field": "title",
                    "scope": "product",
                    "disposition": "applicable",
                    "included": True,
                },
                {
                    "change_id": "vendor-change",
                    "value_path": "vendor",
                    "field": "vendor",
                    "scope": "product",
                    "disposition": "applicable",
                    "included": True,
                },
            ],
        }],
    }

    async def fake_create_plan(draft_id, request, payload, ctx):
        saved = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=shop)
        assert saved["revision"] == 2
        assert saved["products"][0]["title"] == "Grid edited"
        return {"plan": {
            "plan_id": "rebuilt-grid-plan",
            "draft_id": draft_id,
            "shop_domain": shop,
            "draft_revision": 2,
            "version": 2,
            "status": "review",
            "review_contract_version": 2,
            "items": [{
                "item_id": "new-item-id",
                "source_index": 0,
                "selectable": True,
                "selected": True,
                "review_changes": [
                    {
                        "change_id": "title-change",
                        "disposition": "applicable",
                        "required": False,
                        "included": True,
                    },
                    {
                        "change_id": "vendor-change",
                        "disposition": "applicable",
                        "required": False,
                        "included": True,
                    },
                ],
            }],
        }}

    monkeypatch.setattr(sessions_api, "create_mutation_plan", fake_create_plan)
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    headers = {"x-shop-domain": shop}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session = created.json()["session"]
        patched = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operations": [
                    {
                        "type": "value_edit",
                        "change_id": "title-change",
                        "value_path": "title",
                        "value": "Grid edited",
                    },
                    {
                        "type": "selection",
                        "item_id": "working-item",
                        "selected": True,
                        "included_change_ids": ["title-change"],
                    },
                ],
            },
        )
        applied = await client.post(
            f"{base}/{session['session_id']}/apply",
            headers=headers,
            json={"expected_edit_revision": patched.json()["session"]["edit_revision"]},
        )

    assert applied.status_code == 200, applied.text
    payload = applied.json()
    canonical = service.get_file(canonical_id)
    assert "output_file_id" not in payload
    assert "output_filename" not in payload
    assert "workbook_revision" not in payload
    assert canonical["content_revision"] == original_revision
    assert canonical["content"] == b"canonical-before-grid-apply"
    assert payload["draft"]["revision"] == 2
    rebuilt_item = payload["plan"]["items"][0]
    assert rebuilt_item["selected"] is True
    assert [row["included"] for row in rebuilt_item["review_changes"]] == [True, False]
    latest_item = service.get_latest_mutation_plan(draft_id, shop_domain=shop)["items"][0]
    assert [row["included"] for row in latest_item["review_changes"]] == [True, False]


@pytest.mark.asyncio
async def test_grid_apply_retry_resumes_exact_partial_save_without_new_revision(monkeypatch):
    import api.agents.review_edit_sessions as sessions_api

    service = get_app_context().services.supabase._service
    shop = "grid-apply-retry.myshopify.com"
    draft_id = "grid-apply-retry-draft"
    location_id = "gid://shopify/Location/7"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [{"title": "Original", "vendor": "Original vendor"}],
    }
    service.mutation_plans["grid-apply-retry-plan"] = {
        "plan_id": "grid-apply-retry-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "draft_revision": 1,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "inventory_context": {
            "selected_location": {"id": location_id, "name": "Main"}
        },
        "items": [{
            "item_id": "retry-item",
            "source_index": 0,
            "selectable": True,
            "selected": True,
            "review_changes": [{
                "change_id": "retry-title",
                "value_path": "title",
                "field": "title",
                "scope": "product",
                "disposition": "applicable",
                "included": True,
            }],
        }],
    }
    rebuild_calls = 0

    async def flaky_create_plan(draft_id, request, payload, ctx):
        nonlocal rebuild_calls
        rebuild_calls += 1
        saved = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=shop)
        assert saved["revision"] == 2
        assert saved["products"][0]["title"] == "Retry survives"
        assert payload == {"inventory_location_id": location_id}
        if rebuild_calls == 1:
            raise HTTPException(status_code=503, detail="simulated rebuild failure")
        return {"plan": {
            "plan_id": "retry-rebuilt-plan",
            "draft_id": draft_id,
            "shop_domain": shop,
            "draft_revision": 2,
            "version": 2,
            "status": "review",
            "review_contract_version": 2,
            "inventory_context": {
                "selected_location": {"id": location_id, "name": "Main"}
            },
            "items": [{
                "item_id": "retry-rebuilt-item",
                "source_index": 0,
                "selectable": True,
                "selected": True,
                "review_changes": [{
                    "change_id": "retry-title",
                    "disposition": "applicable",
                    "required": False,
                    "included": True,
                }],
            }],
        }}

    monkeypatch.setattr(sessions_api, "create_mutation_plan", flaky_create_plan)
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    headers = {"x-shop-domain": shop}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session = created.json()["session"]
        edited = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operation": {
                    "type": "value_edit",
                    "change_id": "retry-title",
                    "value_path": "title",
                    "value": "Retry survives",
                },
            },
        )
        apply_url = f"{base}/{session['session_id']}/apply"
        apply_body = {
            "expected_edit_revision": edited.json()["session"]["edit_revision"]
        }
        failed = await client.post(apply_url, headers=headers, json=apply_body)
        revision_after_failure = service.product_drafts[draft_id]["revision"]
        retried = await client.post(apply_url, headers=headers, json=apply_body)

    assert failed.status_code == 503
    assert revision_after_failure == 2
    assert retried.status_code == 200, retried.text
    assert retried.json()["draft"]["revision"] == 2
    assert retried.json()["draft"]["products"][0]["title"] == "Retry survives"
    assert retried.json()["session"]["status"] == "applied"
    assert "_apply_checkpoint" not in retried.json()["session"]["working_plan"]
    assert retried.json()["plan"]["inventory_context"]["selected_location"]["id"] == location_id
    assert rebuild_calls == 2


@pytest.mark.asyncio
async def test_grid_apply_keeps_unrelated_stale_draft_conflict():
    service = get_app_context().services.supabase._service
    shop = "grid-apply-stale.myshopify.com"
    draft_id = "grid-apply-stale-draft"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "products": [{"title": "Original"}],
    }
    service.mutation_plans["grid-apply-stale-plan"] = {
        "plan_id": "grid-apply-stale-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "draft_revision": 1,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [],
    }
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    headers = {"x-shop-domain": shop}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session = created.json()["session"]
        service.product_drafts[draft_id] = {
            **service.product_drafts[draft_id],
            "revision": 2,
            "products": [{"title": "Concurrent change"}],
        }
        applied = await client.post(
            f"{base}/{session['session_id']}/apply",
            headers=headers,
            json={"expected_edit_revision": 1},
        )

    assert applied.status_code == 409
    assert applied.json()["detail"] == {
        "code": "REVIEW_REQUIRED",
        "message": "Draft revision is stale",
    }


@pytest.mark.asyncio
async def test_grid_discard_leaves_draft_and_canonical_workbook_unchanged():
    service = get_app_context().services.supabase._service
    shop = "grid-discard.myshopify.com"
    draft_id = "grid-discard-draft"
    canonical_id = "grid-discard-canonical"
    canonical_bytes = b"canonical-must-not-change"
    service.save_file(
        canonical_id,
        "generated-import.xlsx",
        canonical_bytes,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_origin="workflow_output",
        shop_domain=shop,
    )
    canonical_revision = service.get_file(canonical_id)["content_revision"]
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "output_file_id": canonical_id,
        "output_filename": "generated-import.xlsx",
        "products": [{"title": "Base title"}],
    }
    service.mutation_plans["grid-discard-plan"] = {
        "plan_id": "grid-discard-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "item-1",
            "source_index": 0,
            "selectable": True,
            "selected": True,
            "review_changes": [{
                "change_id": "title-change",
                "value_path": "title",
                "field": "title",
                "scope": "product",
                "disposition": "applicable",
                "included": True,
            }],
        }],
    }
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    headers = {"x-shop-domain": shop}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            base, headers=headers, json={"surface": "grid", "base_revision": 1}
        )
        session = created.json()["session"]
        patched = await client.patch(
            f"{base}/{session['session_id']}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operation": {
                    "type": "value_edit",
                    "change_id": "title-change",
                    "value_path": "title",
                    "value": "Unapplied title",
                },
            },
        )
        assert patched.status_code == 200, patched.text
        discarded = await client.delete(
            f"{base}/{session['session_id']}", headers=headers
        )

    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["session"]["status"] == "discarded"
    draft = service.get_product_draft(draft_id, shop_domain=shop)
    canonical = service.get_file(canonical_id)
    assert draft["revision"] == 1
    assert draft["products"] == [{"title": "Base title"}]
    assert canonical["content"] == canonical_bytes
    assert canonical["content_revision"] == canonical_revision


@pytest.mark.asyncio
async def test_invalid_value_operations_return_atomic_structured_422():
    service = get_app_context().services.supabase._service
    shop = "atomic-validation.myshopify.com"
    draft_id = "atomic-validation-draft"
    _seed_durable_history_review(service, shop, draft_id)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        rejected = await client.patch(
            f"{base}/{session_id}",
            headers=headers,
            json={
                "expected_edit_revision": 1,
                "operations": [
                    {"type": "value_edit", "change_id": "vendor-change", "value_path": "vendor", "value": "Changed first"},
                    {"type": "value_edit", "change_id": "title-change", "value_path": "title", "value": ""},
                ],
            },
        )
        resumed = await client.get(base, headers=headers)

    assert rejected.status_code == 422, rejected.text
    detail = rejected.json()["detail"]
    assert detail["code"] == "REVIEW_FIELD_VALIDATION_FAILED"
    assert detail["field_errors"] == [{
        "item_id": "item-1",
        "change_id": "title-change",
        "cell_id": "",
        "value_path": "title",
        "code": "INVALID_REVIEW_VALUE",
        "message": "This field is required.",
        "remedy": "edit",
    }]
    active = resumed.json()["session"]
    assert active["edit_revision"] == 1
    assert active["pending_products"][0]["vendor"] == "Base vendor"
    assert active["history"] == []


@pytest.mark.asyncio
async def test_option_value_rename_cascades_undoes_redoes_and_applies(monkeypatch):
    import api.agents.review_edit_sessions as sessions_api

    service = get_app_context().services.supabase._service
    shop = "option-rename.myshopify.com"
    draft_id = "option-rename-draft"
    canonical_id = "option-rename-workbook"
    service.save_file(
        canonical_id,
        "generated-import.xlsx",
        b"before",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_origin="workflow_output",
        shop_domain=shop,
    )
    previous_revision = service.get_file(canonical_id)["content_revision"]
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": shop,
        "revision": 1,
        "output_file_id": canonical_id,
        "output_filename": "generated-import.xlsx",
        "products": [{
            "handle": "forest-shirt",
            "title": "Forest shirt",
            "options": [{"name": "Color", "values": ["Black", "Forest Green"]}],
            "variants": [
                {"option1": "Black", "sku": "BLACK", "price": 20},
                {"option1": "Forest Green", "sku": "GREEN", "price": 20},
            ],
        }],
    }
    service.mutation_plans["option-rename-plan"] = {
        "plan_id": "option-rename-plan",
        "draft_id": draft_id,
        "shop_domain": shop,
        "version": 1,
        "status": "review",
        "review_contract_version": 2,
        "items": [{
            "item_id": "item-1",
            "source_index": 0,
            "selectable": True,
            "selected": True,
            "review_changes": [{
                "change_id": "color-values",
                "value_path": "option[color].values",
                "field": "values",
                "scope": "option",
                "subject": "Color",
                "proposed": ["Black", "Forest Green"],
                "disposition": "applicable",
                "included": True,
            }, {
                "change_id": "forest-price",
                "value_path": "variant[color-forest-green].price",
                "field": "price",
                "scope": "variant",
                "subject": "Color: Forest Green",
                "proposed": 20,
                "disposition": "applicable",
                "included": True,
            }],
        }],
    }

    async def fake_create_plan(draft_id, request, payload, ctx):
        saved = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=shop)
        assert saved["products"][0]["options"][0]["values"] == ["Black", "Forest Red"]
        assert saved["products"][0]["variants"][1]["option1"] == "Forest Red"
        return {"plan": {
            "plan_id": "option-rename-rebuilt",
            "draft_id": draft_id,
            "shop_domain": shop,
            "draft_revision": 2,
            "review_contract_version": 2,
            "items": [],
        }}

    monkeypatch.setattr(sessions_api, "create_mutation_plan", fake_create_plan)
    headers = {"x-shop-domain": shop}
    base = f"/agents/product-drafts/{draft_id}/review-edit-sessions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(base, headers=headers, json={"surface": "grid", "base_revision": 1})
        session_id = created.json()["session"]["session_id"]
        edited = await client.patch(
            f"{base}/{session_id}", headers=headers,
            json={"expected_edit_revision": 1, "operation": {
                "type": "option_values_edit", "change_id": "color-values",
                "value_path": "option[color].values", "action": "rename",
                "from": "Forest Green", "to": "Forest Red",
            }},
        )
        undone = await client.post(f"{base}/{session_id}/undo", headers=headers, json={"expected_edit_revision": 2})
        redone = await client.post(f"{base}/{session_id}/redo", headers=headers, json={"expected_edit_revision": 3})
        applied = await client.post(f"{base}/{session_id}/apply", headers=headers, json={"expected_edit_revision": 4})

    assert edited.status_code == 200, edited.text
    assert edited.json()["session"]["pending_products"][0]["variants"][1]["option1"] == "Forest Red"
    assert any(
        row["subject"] == "Color: Forest Red"
        for row in edited.json()["session"]["working_plan"]["items"][0]["review_changes"]
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["session"]["pending_products"][0]["variants"][1]["option1"] == "Forest Green"
    assert any(
        row["subject"] == "Color: Forest Green"
        for row in undone.json()["session"]["working_plan"]["items"][0]["review_changes"]
    )
    assert redone.status_code == 200, redone.text
    assert redone.json()["session"]["pending_products"][0]["variants"][1]["option1"] == "Forest Red"
    assert applied.status_code == 200, applied.text
    assert "workbook_revision" not in applied.json()
    assert applied.json()["draft"]["revision"] == 2


def test_legacy_review_edit_session_is_lazily_hydrated_with_safe_history_defaults():
    service = get_app_context().services.supabase._service
    shop = "legacy-review-edits.myshopify.com"
    service.review_edit_sessions["legacy-session"] = {
        "session_id": "legacy-session",
        "draft_id": "legacy-draft",
        "shop_domain": shop,
        "surface": "grid",
        "base_draft_revision": 2,
        "edit_revision": 4,
        "pending_products": [],
        "status": "active",
    }

    session = service.get_review_edit_session("legacy-session", shop_domain=shop)

    assert session is not None
    assert session["working_plan"] == {}
    assert session["grid_state"] == {
        "filterValues": {"productStatus": "all"},
        "expandedItemIds": [],
        "expansionInitialized": False,
        "pagesByItem": {},
        "productPage": 0,
    }
    assert session["history"] == []
    assert session["history_cursor"] == 0
    assert session["base_plan_id"] is None
    assert session["edit_revision"] == 4
    assert service.get_review_edit_session(
        "legacy-session", shop_domain="another-shop.myshopify.com"
    ) is None


def test_review_edit_session_hydration_bounds_history_cursor_and_grid_state():
    service = get_app_context().services.supabase._service
    session = service.save_review_edit_session({
        "session_id": "history-session",
        "draft_id": "history-draft",
        "shop_domain": "history.myshopify.com",
        "surface": "grid",
        "base_draft_revision": 1,
        "edit_revision": 2,
        "pending_products": [],
        "working_plan": {"plan_id": "base-plan", "items": []},
        "grid_state": {
            "filterValues": {},
            "expandedItemIds": ["item-1", 7, "item-1"],
            "pagesByItem": {"item-1": 2, "bad": -1, "boolean": True},
            "workspaceView": "invalid",
        },
        "history": [{"operation": "filters"}, {"operation": "expansion"}],
        "history_cursor": 99,
        "status": "active",
    })

    assert session["base_plan_id"] == "base-plan"
    assert session["history_cursor"] == 0
    assert session["history"] == []
    assert session["grid_state"] == {
        "filterValues": {"productStatus": "all"},
        "expandedItemIds": [],
        "expansionInitialized": False,
        "pagesByItem": {},
        "productPage": 0,
    }

    session["history_cursor"] = -10
    hydrated = service.get_review_edit_session(
        "history-session", shop_domain="history.myshopify.com"
    )
    assert hydrated is not None
    assert hydrated["history_cursor"] == 0


def test_hydration_retains_synthetic_cell_history_and_derives_unapplied_changes():
    service = get_app_context().services.supabase._service
    shop = "cell-history.myshopify.com"
    service.product_drafts["cell-history-draft"] = {
        "draft_id": "cell-history-draft",
        "shop_domain": shop,
        "products": [{"variants": [{"inventory_quantity": 4}]}],
    }
    service.review_edit_sessions["cell-history-session"] = {
        "session_id": "cell-history-session",
        "draft_id": "cell-history-draft",
        "shop_domain": shop,
        "surface": "grid",
        "base_draft_revision": 1,
        "edit_revision": 2,
        "pending_products": [{"variants": [{"inventory_quantity": 9}]}],
        "working_plan": {"items": []},
        "history": [
            {
                "operation_id": "inventory-cell-edit",
                "operation": "cell_edit",
                "before": 4,
                "after": 9,
            }
        ],
        "history_cursor": 1,
        "status": "active",
    }

    hydrated = service.get_review_edit_session(
        "cell-history-session", shop_domain=shop
    )

    assert hydrated is not None
    assert hydrated["history_cursor"] == 1
    assert hydrated["history"][0]["operation"] == "cell_edit"
    assert hydrated["has_unapplied_changes"] is True


def test_zero_history_pending_product_difference_is_recoverable():
    service = get_app_context().services.supabase._service
    shop = "recovered-autosave.myshopify.com"
    service.product_drafts["recovered-autosave-draft"] = {
        "draft_id": "recovered-autosave-draft",
        "shop_domain": shop,
        "products": [{"variants": [{"inventory_quantity": 2}]}],
    }
    service.review_edit_sessions["recovered-autosave-session"] = {
        "session_id": "recovered-autosave-session",
        "draft_id": "recovered-autosave-draft",
        "shop_domain": shop,
        "surface": "grid",
        "base_draft_revision": 1,
        "edit_revision": 2,
        "pending_products": [{"variants": [{"inventory_quantity": 7}]}],
        "working_plan": {"items": []},
        "history": [],
        "history_cursor": 0,
        "status": "active",
    }

    hydrated = service.get_active_review_edit_session(
        "recovered-autosave-draft", shop_domain=shop
    )

    assert hydrated is not None
    assert hydrated["history_cursor"] == 0
    assert hydrated["has_unapplied_changes"] is True


def test_hydration_exposes_malformed_option_value_session_field_error():
    service = get_app_context().services.supabase._service
    session = service.save_review_edit_session({
        "session_id": "malformed-option-session",
        "draft_id": "malformed-option-draft",
        "shop_domain": "malformed-option.myshopify.com",
        "surface": "grid",
        "base_draft_revision": 1,
        "edit_revision": 2,
        "pending_products": [],
        "working_plan": {"items": [{"source_index": 0, "review_changes": [{
            "change_id": "color-values",
            "value_path": "option[color].values",
            "field": "values",
            "scope": "option",
            "proposed": "Black,Forest Red",
        }]}]},
        "status": "active",
    })

    assert session["field_errors"] == [{
        "change_id": "color-values",
        "value_path": "option[color].values",
        "code": "INVALID_LIST",
        "message": "Enter one value per line, or revert this field.",
    }]
