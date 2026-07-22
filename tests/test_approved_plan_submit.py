import json
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app_context import get_app_context
from main import app


SHOP = "approved-plan-submit.myshopify.com"
HEADERS = {"x-shop-domain": SHOP}


def _seed_plan(
    *,
    draft_products: list[dict[str, Any]],
    selected_indexes: list[int],
    status: str = "approved",
) -> tuple[str, str]:
    service = get_app_context().services.supabase._service
    draft_id = f"draft-{uuid.uuid4()}"
    plan_id = f"plan-{uuid.uuid4()}"
    service.product_drafts[draft_id] = {
        "draft_id": draft_id,
        "shop_domain": SHOP,
        "revision": 1,
        "draft_name": "approved-selection.xlsx",
        "products": draft_products,
    }
    service.mutation_plans[plan_id] = {
        "plan_id": plan_id,
        "draft_id": draft_id,
        "shop_domain": SHOP,
        "draft_revision": 1,
        "version": 1,
        "status": status,
        "items": [
            {
                "item_id": f"item-{index}",
                "selected": index in selected_indexes,
                "selectable": True,
                "blockers": [],
                "proposed": product,
            }
            for index, product in enumerate(draft_products)
        ],
    }
    return draft_id, plan_id


def _patch_bulk(monkeypatch, rows: list[Any], captured: dict[str, Any] | None = None):
    import shopify as shopify_module
    import shopify_session_store
    from application.use_cases.processing import submit_products as submit_module

    monkeypatch.setattr(
        shopify_session_store, "get_offline_access_token", lambda _shop: "token"
    )

    def build_jsonl(products):
        if captured is not None:
            captured["products"] = products
        return "\n".join(json.dumps({"input": product}) for product in products)

    async def create_staged_upload(self):
        return {
            "url": "https://upload.invalid",
            "resourceUrl": "staged://selected.jsonl",
            "parameters": [{"name": "key", "value": "selected.jsonl"}],
        }

    async def upload(self, url, parameters, jsonl_data):
        if captured is not None:
            captured["jsonl"] = jsonl_data

    async def run_bulk(self, path):
        return {"id": "gid://shopify/BulkOperation/selected"}

    async def wait_bulk(self, operation_id, **kwargs):
        return {
            "status": "COMPLETED",
            "url": "https://results.invalid",
            "rootObjectCount": len(rows),
            "objectCount": len(rows),
        }

    async def download(shopify, result_url):
        return rows

    monkeypatch.setattr(
        shopify_module.ShopifyClient,
        "build_product_set_jsonl",
        staticmethod(build_jsonl),
    )
    monkeypatch.setattr(
        shopify_module.ShopifyClient, "create_staged_upload", create_staged_upload
    )
    monkeypatch.setattr(shopify_module.ShopifyClient, "upload_to_staged_url", upload)
    monkeypatch.setattr(shopify_module.ShopifyClient, "run_bulk_mutation", run_bulk)
    monkeypatch.setattr(
        shopify_module.ShopifyClient, "wait_for_bulk_operation", wait_bulk
    )
    monkeypatch.setattr(submit_module, "_download_bulk_results", download)


@pytest.mark.asyncio
async def test_approved_products_override_five_product_draft_and_keep_metadata(
    monkeypatch,
):
    draft_products = [
        {"title": "Ignore One"},
        {"title": "Awesome T-Shirt"},
        {"title": "Ignore Two"},
        {"title": "Deluxe Coffee Beans"},
        {"title": "Ignore Three"},
    ]
    draft_id, plan_id = _seed_plan(
        draft_products=draft_products, selected_indexes=[1, 3]
    )
    service = get_app_context().services.supabase._service
    plan = service.mutation_plans[plan_id]
    plan["approved_selection"] = [
        {"item_id": "item-1", "included": True, "included_change_ids": ["title-1"]},
        {"item_id": "item-3", "included": True, "included_change_ids": ["title-3"]},
    ]
    for index in (1, 3):
        plan["items"][index]["execution_proposed"] = draft_products[index]
        plan["items"][index]["review_changes"] = [{
            "change_id": f"title-{index}",
            "value_path": "title",
            "origin": "source",
            "included": True,
            "source_ref": {"source_file_id": "source-file", "sheet": "Products", "cell": f"A{index + 2}"},
        }]
    captured: dict[str, Any] = {}
    _patch_bulk(
        monkeypatch,
        [
            {
                "data": {
                    "productSet": {
                        "product": {
                            "id": "gid://shopify/Product/1",
                            "title": "Awesome T-Shirt",
                        },
                        "userErrors": [],
                    }
                }
            },
            {
                "data": {
                    "productSet": {
                        "product": {
                            "id": "gid://shopify/Product/2",
                            "title": "Deluxe Coffee Beans",
                        },
                        "userErrors": [],
                    }
                }
            },
        ],
        captured,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/agents/submit-products",
            headers=HEADERS,
            data={"plan_id": plan_id, "revision": "1"},
        )

    assert response.status_code == 200, response.text
    assert [product["title"] for product in captured["products"]] == [
        "Awesome T-Shirt",
        "Deluxe Coffee Beans",
    ]
    assert len(captured["jsonl"].splitlines()) == 2
    submitted = get_app_context().services.supabase._service.submitted_documents
    stored = next(iter(submitted.values()))
    assert stored["name"] == "approved-selection.xlsx"
    assert stored["draft_id"] == draft_id
    assert stored["product_count"] == 2
    assert stored["approved_selection"] == plan["approved_selection"]
    assert stored["value_provenance"]["item-1"]["title"]["origin"] == "source"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            {
                "data": {
                    "productSet": {
                        "product": None,
                        "userErrors": [
                            {"field": ["options"], "message": "Option value is invalid"}
                        ],
                    }
                }
            },
            "Option value is invalid",
        ),
        (None, "root row is not an object"),
        ({"data": None}, "missing data object"),
        ({"data": {"productSet": None}}, "missing productSet object"),
        (
            {"data": {"productSet": {"product": None, "userErrors": []}}},
            "neither product data nor user errors were returned",
        ),
    ],
)
async def test_bulk_result_layers_preserve_shopify_errors_and_report_malformed_rows(
    monkeypatch, row, expected
):
    _, plan_id = _seed_plan(
        draft_products=[{"title": "Selected Product"}], selected_indexes=[0]
    )
    _patch_bulk(monkeypatch, [row])
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/agents/submit-products",
            headers=HEADERS,
            data={"plan_id": plan_id, "revision": "1"},
        )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "failed"
    assert expected in result["errors"][0]["message"]
    assert expected in response.json()["error"]


@pytest.mark.asyncio
async def test_repeated_apply_reuses_active_job_and_terminal_failure_requires_review(
    monkeypatch,
):
    import shopify_session_store

    draft_id, plan_id = _seed_plan(
        draft_products=[{"title": "Selected Product"}], selected_indexes=[0]
    )
    monkeypatch.setattr(
        shopify_session_store, "get_offline_access_token", lambda _shop: "token"
    )
    data = {"plan_id": plan_id, "revision": "1", "offload": "true"}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/agents/submit-products", headers=HEADERS, data=data)
        job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"shopify-mutation-job:{plan_id}"))
        service = get_app_context().services.supabase._service
        service.offload_jobs[job_id]["status"] = "running"
        second = await client.post(
            "/agents/submit-products", headers=HEADERS, data=data
        )
        assert second.status_code == 202
        assert second.json()["status"] == "running"
        assert len(service.offload_jobs) == 1
        service.offload_jobs[job_id].update(
            status="succeeded",
            result={
                "submitted_id": "submitted-replay",
                "success_count": 1,
                "draft_id": draft_id,
            },
        )
        service.mutation_plans[plan_id]["status"] = "completed"
        replay = await client.post(
            "/agents/submit-products", headers=HEADERS, data=data
        )
        assert replay.status_code == 200
        assert replay.json()["submitted_id"] == "submitted-replay"
        service.offload_jobs[job_id]["status"] = "failed"
        service.mutation_plans[plan_id]["status"] = "failed"
        failed = await client.post(
            "/agents/submit-products", headers=HEADERS, data=data
        )

    assert first.status_code == 202
    assert failed.status_code == 409
    assert failed.json()["detail"]["code"] == "REVIEW_REQUIRED"
    assert service.offload_jobs[job_id]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_status"),
    [("approved", 200), ("executing", 200), ("failed", 409), ("partially_applied", 409)],
)
async def test_approval_is_idempotent_only_for_approved_or_active_plans(
    monkeypatch, status, expected_status
):
    import api.agents.mutation_plans as plans_api

    draft_id, plan_id = _seed_plan(
        draft_products=[{"title": "Selected Product"}],
        selected_indexes=[0],
        status=status,
    )

    async def unexpected_offline_client(_shop):
        raise AssertionError("idempotent or terminal approval must not reacquire a client")

    monkeypatch.setattr(plans_api, "_offline_client", unexpected_offline_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/agents/product-drafts/{draft_id}/mutation-plan/approve",
            headers=HEADERS,
            json={"plan_id": plan_id, "acknowledge_unapplied": True},
        )

    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json()["plan"]["status"] == status
    else:
        assert response.json()["detail"]["code"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("failing_component", ["plan", "run", "draft"])
def test_failure_state_updates_are_independent(monkeypatch, failing_component):
    import application.services.offload_worker as worker_module
    from application.services.offload_worker import OffloadWorker

    ctx = get_app_context()
    service = ctx.services.supabase._service
    draft_id, plan_id = _seed_plan(
        draft_products=[{"title": "Selected Product"}],
        selected_indexes=[0],
        status="executing",
    )
    attempts: list[str] = []
    original_save_plan = service.save_mutation_plan

    def save_plan(plan):
        attempts.append("plan")
        assert "execution_error" not in plan
        if failing_component == "plan":
            raise RuntimeError("plan persistence failed")
        return original_save_plan(plan)

    def finalize_run(*args, **kwargs):
        attempts.append("run")
        if failing_component == "run":
            raise RuntimeError("run persistence failed")

    def save_draft(**kwargs):
        attempts.append("draft")
        if failing_component == "draft":
            raise RuntimeError("draft persistence failed")

    monkeypatch.setattr(service, "save_mutation_plan", save_plan)
    monkeypatch.setattr(ctx.supabase.runs, "finalize_run", finalize_run)
    monkeypatch.setattr(worker_module, "_save_draft_state", save_draft)
    worker = OffloadWorker(ctx=ctx, queue_name="offload", worker_id="worker-test")
    worker._mark_related_failure(
        {
            "job_type": "shopify_submit",
            "run_id": "run-failure-isolation",
            "draft_id": draft_id,
            "shop_domain": SHOP,
            "payload": {"plan_id": plan_id},
        },
        "Shopify rejected the selected item",
    )

    assert attempts == ["plan", "run", "draft"]
