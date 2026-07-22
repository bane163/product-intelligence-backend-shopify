"""Review and approval API for production-safe Shopify changes."""

from __future__ import annotations

import uuid
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

import shopify_session_store
from app_context import AppContext, get_ctx
from application.domain.shopify_mutation_plan import (
    applicable_changes,
    materialize_execution_proposed,
    merge_structured_product,
    preserve_blank_fields,
    review_changes,
    validate_product,
)
from shopify import SHOPIFY_REAUTH_REQUIRED, ShopifyClient
from .utils import require_authenticated_shop_domain

router = APIRouter()
LOG = logging.getLogger(__name__)
REVIEW_REQUIRED = "REVIEW_REQUIRED"
UNSUPPORTED_REVIEW_CONTRACT = "UNSUPPORTED_REVIEW_CONTRACT"

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
    LOG.warning("import_review_conflict message=%s", message)
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


async def _repair_legacy_review_evidence(
    draft: dict[str, Any], previous: dict[str, Any] | None, tenant: str, ctx: AppContext
) -> dict[str, Any]:
    """Re-verify retained source evidence without advancing the value revision."""
    if not previous or previous.get("status") != "review":
        return draft
    refs_need_repair = any(
        isinstance(ref, dict) and (not ref.get("source_file_id") or not ref.get("value_path"))
        for product in _products(draft)
        for ref in (product.get("source_refs") if isinstance(product.get("source_refs"), list) else [])
    )
    if previous.get("review_contract_version") == 2 and not refs_need_repair:
        return draft
    file_id = str(draft.get("input_file_id") or "")
    source = ctx.supabase.file.get_file(file_id) if file_id else None
    if not isinstance(source, dict) or not isinstance(source.get("content"), (bytes, bytearray)):
        return draft
    products = deepcopy(_products(draft))
    filename = str(draft.get("input_filename") or source.get("name") or file_id)
    lower_name = filename.casefold()
    if lower_name.endswith((".xlsx", ".xlsm", ".csv")):
        from application.services.spreadsheet_source_refs import enrich_spreadsheet_source_refs

        enrich_spreadsheet_source_refs(products, bytes(source["content"]), filename, source_file_id=file_id)
    elif lower_name.endswith(".pdf") and ctx.services.document_layout is not None:
        from application.services.pdf_source_refs import verify_pdf_source_refs

        layout = await ctx.services.document_layout.analyze_pdf(bytes(source["content"]), file_id)
        verify_pdf_source_refs(products, layout, file_id)
    else:
        return draft
    for product in products:
        origins = product.get("value_origins") if isinstance(product.get("value_origins"), dict) else {}
        for ref in product.get("source_refs") if isinstance(product.get("source_refs"), list) else []:
            if isinstance(ref, dict) and ref.get("value_path"):
                origins.setdefault(str(ref["value_path"]), "source")
        if origins:
            product["value_origins"] = origins
    saved = ctx.supabase.drafts.save_product_draft(
        draft_id=str(draft["draft_id"]), run_id=draft.get("run_id"),
        import_mode=str(draft.get("import_mode") or "auto"), draft_name=draft.get("draft_name"),
        shop_domain=tenant, input_file_id=draft.get("input_file_id"), input_filename=draft.get("input_filename"),
        output_file_id=draft.get("output_file_id"), output_filename=draft.get("output_filename"),
        extraction_status=draft.get("extraction_status"), extraction_run_id=draft.get("extraction_run_id"),
        extraction_error=draft.get("extraction_error"), products=products,
        revision=int(draft.get("revision") or 1),
    )
    LOG.info("import_review_legacy_evidence_rebuilt draft_id=%s", draft.get("draft_id"))
    return saved


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
    previous = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    draft = await _repair_legacy_review_evidence(draft, previous, tenant, ctx)
    previous_by_source_index = {
        int(item.get("source_index")): item
        for item in (previous or {}).get("items", [])
        if isinstance(item, dict) and isinstance(item.get("source_index"), int)
    }
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
            prior_item = previous_by_source_index.get(index)
            prior_inclusion = {
                str(row.get("change_id")): bool(row.get("included"))
                for row in (prior_item or {}).get("review_changes", [])
                if isinstance(row, dict) and row.get("change_id")
            }
            for row in exact_review:
                if row.get("required"):
                    row["included"] = row.get("disposition") == "applicable"
                elif row.get("change_id") in prior_inclusion:
                    row["included"] = prior_inclusion[str(row["change_id"])]
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
                    "selected": bool(
                        selectable
                        and ((prior_item or {}).get("selected", True))
                    ),
                    "remedy": None,
                    "execution": None,
                }
            )
    finally:
        await client.close()
    plan = {
        "plan_id": str(uuid.uuid4()),
        "draft_id": draft_id,
        "shop_domain": tenant,
        "draft_revision": int(draft.get("revision") or 1),
        "version": int((previous or {}).get("version") or 0) + 1,
        "status": "review",
        "currency_code": currency_code,
        "review_contract_version": 2,
        "items": items,
        "approved_at": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    review_rows = [row for item in items for row in item.get("review_changes", []) if isinstance(row, dict)]
    unknown_count = sum(row.get("origin") == "unknown" for row in review_rows)
    LOG.info(
        "import_review_plan_built draft_id=%s version=%s rows=%s unknown_provenance=%s",
        draft_id,
        plan["version"],
        len(review_rows),
        unknown_count,
    )
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
    items = list(plan.get("items") or [])
    item_selections = payload.get("item_selections")
    if item_selections is not None:
        if plan.get("review_contract_version") != 2:
            raise HTTPException(
                status_code=409,
                detail={"code": UNSUPPORTED_REVIEW_CONTRACT, "message": "Regenerate this review plan before selecting fields"},
            )
        if not isinstance(item_selections, list) or not all(isinstance(x, dict) for x in item_selections):
            raise HTTPException(status_code=400, detail="item_selections must be an array of objects")
        selections = {str(entry.get("item_id") or ""): entry for entry in item_selections}
        valid_ids = {str(item.get("item_id")) for item in items}
        if set(selections) - valid_ids:
            raise _review_error("Selection contains an unknown product")
        for item in items:
            entry = selections.get(str(item.get("item_id")))
            if entry is None:
                continue
            included = entry.get("included")
            change_ids = entry.get("included_change_ids")
            if not isinstance(included, bool) or not isinstance(change_ids, list) or not all(isinstance(x, str) for x in change_ids):
                raise HTTPException(status_code=400, detail="Each item selection requires included and included_change_ids")
            if included and not item.get("selectable"):
                raise _review_error("Selection contains a product that cannot be applied")
            rows = [row for row in item.get("review_changes", []) if isinstance(row, dict)]
            known = {str(row.get("change_id")) for row in rows if row.get("change_id")}
            requested_change_ids = set(change_ids)
            if requested_change_ids - known:
                raise _review_error("Selection contains an unknown change")
            required = {
                str(row.get("change_id"))
                for row in rows
                if row.get("required") and row.get("disposition") == "applicable"
            }
            disallowed = {
                str(row.get("change_id"))
                for row in rows
                if row.get("disposition") != "applicable"
            }
            if included and not required.issubset(requested_change_ids):
                raise _review_error("Required or structurally dependent changes cannot be excluded")
            if requested_change_ids & disallowed:
                raise _review_error("Blocked or deferred changes cannot be included")
            item["selected"] = bool(included)
            for row in rows:
                row["included"] = bool(
                    included
                    and row.get("disposition") == "applicable"
                    and str(row.get("change_id")) in requested_change_ids
                )
            item["review_changes"] = rows
    else:
        requested = payload.get("selected_item_ids")
        if not isinstance(requested, list) or not all(isinstance(x, str) for x in requested):
            raise HTTPException(status_code=400, detail="selected_item_ids must be an array of strings")
        selected_ids = set(requested)
        valid_selectable = {str(x.get("item_id")) for x in items if x.get("selectable")}
        if selected_ids - valid_selectable:
            raise _review_error("Selection contains an item that cannot be applied")
        for item in items:
            selected = bool(item.get("selectable") and item.get("item_id") in selected_ids)
            item["selected"] = selected
            for row in item.get("review_changes", []):
                if isinstance(row, dict):
                    row["included"] = bool(selected and row.get("disposition") == "applicable")
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
        "items": [
            {
                **item,
                "execution_proposed": materialize_execution_proposed(item)
                if item.get("selected")
                else None,
            }
            for item in plan.get("items", [])
        ],
        "approved_selection": [
            {
                "item_id": item.get("item_id"),
                "included": bool(item.get("selected")),
                "included_change_ids": [
                    row.get("change_id")
                    for row in item.get("review_changes", [])
                    if isinstance(row, dict) and row.get("included")
                ],
            }
            for item in plan.get("items", [])
        ],
        "status": "approved",
        "approved_at": _now(),
        "updated_at": _now(),
    }
    _service(ctx).save_mutation_plan(approved)
    return {"plan": approved}
