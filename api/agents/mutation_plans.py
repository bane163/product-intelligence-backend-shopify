"""Review and approval API for production-safe Shopify changes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

import shopify_session_store
from app_context import AppContext, get_ctx
from application.domain.shopify_mutation_plan import (
    applicable_changes,
    merge_structured_product,
    preserve_blank_fields,
    review_changes,
    validate_product,
)
from shopify import SHOPIFY_REAUTH_REQUIRED, ShopifyClient
from .utils import require_authenticated_shop_domain

router = APIRouter()
REVIEW_REQUIRED = "REVIEW_REQUIRED"

ACCESS_SCOPES_QUERY = """query MutationPlanAccessScopes {
  currentAppInstallation { accessScopes { handle } }
  shop { currencyCode }
}"""
PRODUCT_BY_HANDLE_QUERY = """query MutationPlanProductByHandle($query: String!) {
  products(first: 2, query: $query) { nodes {
    id title handle descriptionHtml vendor productType status tags
    seo { title description }
    options { id name position optionValues { id name } }
    variants(first: 250) { nodes {
      id title price compareAtPrice sku barcode taxable taxCode inventoryPolicy
      selectedOptions { name value }
      inventoryItem { id sku unitCost { amount } tracked requiresShipping measurement { weight { value unit } } }
    } pageInfo { hasNextPage endCursor } }
    metafields(first: 250) { nodes { id namespace key value type } }
  } }
}"""
PRODUCT_VARIANTS_PAGE_QUERY = """query MutationPlanProductVariants($id: ID!, $after: String!) {
  product(id: $id) { variants(first: 250, after: $after) { nodes {
    id title price compareAtPrice sku barcode taxable taxCode inventoryPolicy
    selectedOptions { name value }
    inventoryItem { id sku unitCost { amount } tracked requiresShipping measurement { weight { value unit } } }
  } pageInfo { hasNextPage endCursor } } }
}"""


def _service(ctx: AppContext) -> Any:
    return getattr(ctx.supabase, "_service", ctx.supabase)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_error(message: str, *, status_code: int = 409) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": REVIEW_REQUIRED, "message": message}
    )


async def _offline_client(shop: str) -> ShopifyClient:
    try:
        token = shopify_session_store.get_offline_access_token(shop)
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": SHOPIFY_REAUTH_REQUIRED, "action": "/auth/login"},
        ) from exc
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"code": SHOPIFY_REAUTH_REQUIRED, "action": "/auth/login"},
        )
    return ShopifyClient(shop=shop, token=token)


def _products(draft: dict[str, Any]) -> list[dict[str, Any]]:
    raw = draft.get("products")
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


@router.put("/product-drafts/{draft_id}", summary="Persist reviewed draft edits")
async def update_product_draft(
    draft_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    existing = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=tenant)
    if not isinstance(existing, dict):
        raise HTTPException(status_code=404, detail="Draft not found")
    expected = payload.get("revision")
    current_revision = int(existing.get("revision") or 1)
    if expected is not None and int(expected) != current_revision:
        raise _review_error("Draft revision is stale")
    products = payload.get("products")
    if not isinstance(products, list) or not all(isinstance(x, dict) for x in products):
        raise HTTPException(
            status_code=400, detail="products must be an array of objects"
        )
    saved = ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=existing.get("run_id"),
        import_mode=str(existing.get("import_mode") or "auto"),
        draft_name=existing.get("draft_name"),
        shop_domain=tenant,
        input_file_id=existing.get("input_file_id"),
        input_filename=existing.get("input_filename"),
        output_file_id=existing.get("output_file_id"),
        output_filename=existing.get("output_filename"),
        extraction_status=existing.get("extraction_status"),
        extraction_run_id=existing.get("extraction_run_id"),
        extraction_error=existing.get("extraction_error"),
        products=products,
        revision=current_revision + 1,
    )
    plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if plan and plan.get("status") == "approved":
        _service(ctx).save_mutation_plan(
            {**plan, "status": "stale", "approved_at": None, "updated_at": _now()}
        )
    return {"draft": saved}


@router.post(
    "/product-drafts/{draft_id}/mutation-plan",
    summary="Generate Shopify mutation preflight",
)
async def create_mutation_plan(
    draft_id: str, request: Request, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request)
    draft = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=tenant)
    if not isinstance(draft, dict):
        raise HTTPException(status_code=404, detail="Draft not found")
    client = await _offline_client(tenant)
    try:
        scopes_result = await client.graphql(ACCESS_SCOPES_QUERY)
        scopes = (
            scopes_result.get("data", {})
            .get("currentAppInstallation", {})
            .get("accessScopes", [])
        )
        currency_code = str(
            scopes_result.get("data", {}).get("shop", {}).get("currencyCode") or "USD"
        ).upper()
        if "write_products" not in {
            x.get("handle") for x in scopes if isinstance(x, dict)
        }:
            raise _review_error(
                "The offline session does not have write_products access",
                status_code=403,
            )
        items: list[dict[str, Any]] = []
        for index, product in enumerate(_products(draft)):
            handle = str(product.get("handle") or "").strip().lower()
            nodes: list[dict[str, Any]] = []
            if handle:
                result = await client.graphql(
                    PRODUCT_BY_HANDLE_QUERY, {"query": f"handle:{handle}"}
                )
                raw_nodes = result.get("data", {}).get("products", {}).get("nodes", [])
                nodes = [
                    x
                    for x in raw_nodes
                    if isinstance(x, dict)
                    and str(x.get("handle") or "").lower() == handle
                ]
            live = nodes[0] if len(nodes) == 1 else None
            if live:
                variants_connection = (
                    live.get("variants")
                    if isinstance(live.get("variants"), dict)
                    else {}
                )
                variant_nodes = list(variants_connection.get("nodes") or [])
                page_info = (
                    variants_connection.get("pageInfo")
                    if isinstance(variants_connection.get("pageInfo"), dict)
                    else {}
                )
                while (
                    page_info.get("hasNextPage")
                    and page_info.get("endCursor")
                    and len(variant_nodes) <= 2048
                ):
                    page_result = await client.graphql(
                        PRODUCT_VARIANTS_PAGE_QUERY,
                        {
                            "id": live["id"],
                            "after": page_info["endCursor"],
                        },
                    )
                    page = (
                        page_result.get("data", {})
                        .get("product", {})
                        .get("variants", {})
                    )
                    variant_nodes.extend(
                        x for x in page.get("nodes", []) if isinstance(x, dict)
                    )
                    page_info = (
                        page.get("pageInfo")
                        if isinstance(page.get("pageInfo"), dict)
                        else {}
                    )
                live["variants"] = {"nodes": variant_nodes}
            operation = "update" if live else "create"
            blockers, unapplied = validate_product(
                product, creating=operation == "create"
            )
            intent = (
                str(product.get("operation") or product.get("intent") or "")
                .strip()
                .lower()
            )
            if intent in {"delete", "remove"}:
                operation = "blocked"
                blockers.append(
                    {"code": "DELETE_NOT_SUPPORTED", "remedies": ["archive", "skip"]}
                )
            elif intent == "create" and live:
                blockers.append(
                    {"code": "CREATE_HANDLE_COLLISION", "remedies": ["link", "skip"]}
                )
            elif intent in {"update", "archive"} and not live:
                blockers.append(
                    {
                        "code": "UPDATE_TARGET_NOT_FOUND",
                        "remedies": ["edit_handle", "skip"],
                    }
                )
            proposed = preserve_blank_fields(live or {}, product)
            # Discard all extracted identity values. Only the live lookup may
            # supply mutation authority.
            proposed.pop("shopify_gid", None)
            proposed.pop("id", None)
            if live:
                proposed["id"] = live["id"]
            if operation == "create" and not proposed.get("status"):
                proposed["status"] = "DRAFT"
            proposed, structured_blockers, structured_unapplied = (
                merge_structured_product(live, product, proposed)
            )
            blockers.extend(structured_blockers)
            unapplied.extend(structured_unapplied)
            changes = applicable_changes(live, proposed)
            exact_review = review_changes(
                live, product, proposed, blockers=blockers, unapplied=unapplied
            )
            if blockers:
                operation = "blocked"
            elif live and not changes:
                operation = "unchanged"
            selectable = operation in {"create", "update"} and bool(changes)
            item_id = str(uuid.uuid4())
            items.append(
                {
                    "item_id": item_id,
                    "currency_code": currency_code,
                    "source_index": index,
                    "source_evidence": product.get("source_refs") or [],
                    "live_snapshot": live,
                    "proposed": proposed,
                    "operation": operation,
                    "resolved_product_id": live.get("id") if live else None,
                    "blockers": blockers,
                    "unapplied": unapplied,
                    "applicable_changes": changes,
                    "review_changes": exact_review,
                    "selectable": selectable,
                    "selected": selectable,
                    "remedy": None,
                    "execution": None,
                }
            )
    finally:
        await client.close()
    previous = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    plan = {
        "plan_id": str(uuid.uuid4()),
        "draft_id": draft_id,
        "shop_domain": tenant,
        "draft_revision": int(draft.get("revision") or 1),
        "version": int((previous or {}).get("version") or 0) + 1,
        "status": "review",
        "currency_code": currency_code,
        "items": items,
        "approved_at": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    _service(ctx).save_mutation_plan(plan)
    return {"plan": plan}


@router.get(
    "/product-drafts/{draft_id}/mutation-plan",
    summary="Get latest Shopify mutation plan",
)
async def get_mutation_plan(
    draft_id: str, request: Request, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request)
    plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if not plan:
        raise HTTPException(status_code=404, detail="Mutation plan not found")
    return {"plan": plan}


@router.patch("/product-drafts/{draft_id}/mutation-plan/selection")
async def patch_mutation_plan_selection(
    draft_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    plan = _service(ctx).get_mutation_plan(
        str(payload.get("plan_id") or ""), shop_domain=tenant
    )
    if not plan or plan.get("draft_id") != draft_id or plan.get("status") != "review":
        raise _review_error("Plan is missing or no longer editable")
    requested = payload.get("selected_item_ids")
    if not isinstance(requested, list) or not all(
        isinstance(x, str) for x in requested
    ):
        raise HTTPException(
            status_code=400, detail="selected_item_ids must be an array of strings"
        )
    selected_ids = set(requested)
    items = list(plan.get("items") or [])
    for item in items:
        item["selected"] = bool(
            item.get("selectable") and item.get("item_id") in selected_ids
        )
    if selected_ids - {str(x.get("item_id")) for x in items if x.get("selectable")}:
        raise _review_error("Selection contains an item that cannot be applied")
    plan = {**plan, "items": items, "updated_at": _now()}
    _service(ctx).save_mutation_plan(plan)
    return {"plan": plan}


@router.post("/product-drafts/{draft_id}/mutation-plan/approve")
async def approve_mutation_plan(
    draft_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default={}),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    draft = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=tenant)
    if (
        not plan
        or plan.get("plan_id") != payload.get("plan_id")
        or not draft
        or int(plan.get("draft_revision") or 0) != int(draft.get("revision") or 1)
    ):
        raise _review_error("Plan is stale")
    selected = [x for x in plan.get("items", []) if x.get("selected")]
    if not selected:
        raise _review_error("Select at least one valid change")
    if any(x.get("blockers") for x in selected):
        raise _review_error("Selected changes contain blockers")
    if any(x.get("unapplied") for x in selected) and not payload.get(
        "acknowledge_unapplied"
    ):
        raise _review_error("Deferred fields must be acknowledged")
    plan_status = str(plan.get("status") or "").strip().lower()
    if plan_status in {"approved", "executing"}:
        return {"plan": plan}
    if plan_status != "review":
        raise _review_error("Execution is terminal; refresh Shopify review")
    approval_client = await _offline_client(
        tenant
    )  # approval cannot outlive a missing credential
    await approval_client.close()
    approved = {
        **plan,
        "status": "approved",
        "approved_at": _now(),
        "updated_at": _now(),
    }
    _service(ctx).save_mutation_plan(approved)
    return {"plan": approved}
