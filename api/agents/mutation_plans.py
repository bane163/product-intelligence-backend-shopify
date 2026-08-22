"""Review and approval API for production-safe Shopify changes."""

from __future__ import annotations

import uuid
import logging
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

import shopify_session_store
from app_context import AppContext, get_ctx
from application.domain.shopify_mutation_plan import (
    build_review_plan_item,
    infer_positional_option_dimensions,
    materialize_execution_proposed,
    materialize_inventory_operations,
    merge_structured_product,
    normalize_review_plan_rows,
    preserve_blank_fields,
    validate_inventory_quantity,
    validate_product,
)
from application.domain.extraction_quality import (
    QUALITY_STATUS_LIVE_PENDING,
    QUALITY_STATUS_READY,
    build_quality_report,
    issues_for_product,
)
from application.domain.sku_autocorrection import (
    carry_forward_required_live_values,
    generate_missing_skus,
    variant_option_pairs,
)
from application.domain.review_value_paths import (
    canonical_value_path,
    normalized_path_part,
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
VARIANTS_BY_SKU_QUERY = """query MutationPlanVariantsBySku($query: String!) {
  productVariants(first: 10, query: $query) { nodes { id sku product { id handle } } }
}"""
LOCATIONS_QUERY = """query MutationPlanLocations {
  locations(first: 250, includeLegacy: false) { nodes {
    id name isActive isFulfillmentService
    address { address1 address2 city provinceCode zip countryCode }
  } }
}"""
INVENTORY_SNAPSHOT_QUERY = """query MutationPlanInventory($id: ID!, $locationId: ID!) {
  product(id: $id) { variants(first: 250) { nodes {
    id selectedOptions { name value }
    inventoryItem { id tracked inventoryLevel(locationId: $locationId) {
      id quantities(names: ["available"]) { name quantity }
    } }
  } pageInfo { hasNextPage endCursor } } }
}"""
INVENTORY_SNAPSHOT_PAGE_QUERY = """query MutationPlanInventoryPage($id: ID!, $locationId: ID!, $after: String!) {
  product(id: $id) { variants(first: 250, after: $after) { nodes {
    id selectedOptions { name value }
    inventoryItem { id tracked inventoryLevel(locationId: $locationId) {
      id quantities(names: ["available"]) { name quantity }
    } }
  } pageInfo { hasNextPage endCursor } } }
}"""
INVENTORY_SCOPES = {"write_inventory", "read_inventory", "read_locations"}


def _has_inventory(products: list[dict[str, Any]]) -> bool:
    for product in products:
        raw = product.get("variants")
        if isinstance(raw, dict):
            raw = raw.get("nodes")
        for variant in raw if isinstance(raw, list) else []:
            if not isinstance(variant, dict):
                continue
            for key in ("inventory_quantity", "inventoryQuantity"):
                if key not in variant or variant.get(key) in (None, ""):
                    continue
                try:
                    validate_inventory_quantity(variant.get(key))
                except ValueError:
                    continue
                return True
    return False


def _location_view(node: dict[str, Any]) -> dict[str, Any]:
    address = node.get("address") if isinstance(node.get("address"), dict) else {}
    lines = [
        address.get("address1"),
        address.get("address2"),
        ", ".join(
            filter(
                None,
                [address.get("city"), address.get("provinceCode"), address.get("zip")],
            )
        ),
        address.get("countryCode"),
    ]
    return {
        "id": str(node.get("id") or ""),
        "name": str(node.get("name") or "Location"),
        **({"address": [str(line) for line in lines if line]} if any(lines) else {}),
    }


def _apply_inventory_snapshot(live: dict[str, Any], snapshot: dict[str, Any]) -> None:
    raw_live = (
        live.get("variants", {}).get("nodes", [])
        if isinstance(live.get("variants"), dict)
        else []
    )
    raw_snapshot = (
        snapshot.get("variants", {}).get("nodes", [])
        if isinstance(snapshot.get("variants"), dict)
        else []
    )
    by_id = {item.get("id"): item for item in raw_snapshot if isinstance(item, dict)}
    for variant in raw_live:
        if not isinstance(variant, dict) or variant.get("id") not in by_id:
            continue
        inventory_item = by_id[variant["id"]].get("inventoryItem")
        if not isinstance(inventory_item, dict):
            continue
        variant["inventoryItem"] = {
            **(variant.get("inventoryItem") or {}),
            **inventory_item,
        }
        level = inventory_item.get("inventoryLevel")
        variant["_inventory_stocked"] = isinstance(level, dict)
        quantities = level.get("quantities") if isinstance(level, dict) else []
        available = next(
            (
                q.get("quantity")
                for q in quantities
                if isinstance(q, dict) and q.get("name") == "available"
            ),
            None,
        )
        if isinstance(available, int):
            variant["inventory_quantity"] = available


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


def _save_quality_state(
    ctx: AppContext,
    draft: dict[str, Any],
    *,
    status: str,
    report: dict[str, Any],
) -> dict[str, Any]:
    return ctx.supabase.drafts.save_product_draft(
        draft_id=str(draft["draft_id"]),
        run_id=draft.get("run_id"),
        import_mode=str(draft.get("import_mode") or "auto"),
        draft_name=draft.get("draft_name"),
        shop_domain=draft.get("shop_domain"),
        input_file_id=draft.get("input_file_id"),
        input_filename=draft.get("input_filename"),
        output_file_id=draft.get("output_file_id"),
        output_filename=draft.get("output_filename"),
        extraction_status=draft.get("extraction_status"),
        extraction_run_id=draft.get("extraction_run_id"),
        extraction_error=draft.get("extraction_error"),
        quality_gate_status=status,
        quality_report={**report, "status": status},
        quality_checked_at=report.get("generated_at"),
        submit_status=draft.get("submit_status"),
        submit_run_id=draft.get("submit_run_id"),
        submit_error=draft.get("submit_error"),
        ai_enhancements_enabled=bool(draft.get("ai_enhancements_enabled", True)),
        revision=int(draft.get("revision") or 1),
        products=_products(draft),
    )


def _save_corrected_products(
    ctx: AppContext, draft: dict[str, Any], products: list[dict[str, Any]]
) -> dict[str, Any]:
    """Persist one atomic autocorrection revision, preserving draft metadata."""
    return ctx.supabase.drafts.save_product_draft(
        draft_id=str(draft["draft_id"]),
        run_id=draft.get("run_id"),
        import_mode=str(draft.get("import_mode") or "auto"),
        draft_name=draft.get("draft_name"),
        shop_domain=draft.get("shop_domain"),
        input_file_id=draft.get("input_file_id"),
        input_filename=draft.get("input_filename"),
        output_file_id=draft.get("output_file_id"),
        output_filename=draft.get("output_filename"),
        extraction_status=draft.get("extraction_status"),
        extraction_run_id=draft.get("extraction_run_id"),
        extraction_error=draft.get("extraction_error"),
        quality_gate_status=QUALITY_STATUS_LIVE_PENDING,
        quality_report=draft.get("quality_report"),
        quality_checked_at=draft.get("quality_checked_at"),
        submit_status=draft.get("submit_status"),
        submit_run_id=draft.get("submit_run_id"),
        submit_error=draft.get("submit_error"),
        ai_enhancements_enabled=bool(draft.get("ai_enhancements_enabled", True)),
        revision=int(draft.get("revision") or 1) + 1,
        products=products,
    )


def _variants(product: dict[str, Any]) -> list[dict[str, Any]]:
    raw = product.get("variants")
    if isinstance(raw, dict):
        raw = raw.get("nodes")
    return (
        [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, list)
        else []
    )


def _mark_derived_sku(product: dict[str, Any], variant_index: int, sku: str) -> None:
    variants = _variants(product)
    variant = variants[variant_index]
    variant["sku"] = sku
    value_path = f"variants[{variant_index}].sku"
    pairs = variant_option_pairs(variant, [])
    subject = " / ".join(f"{name}: {value}" for name, value in pairs)
    canonical_path = (
        f"variant[{normalized_path_part(subject)}].sku" if subject else value_path
    )
    for key in ("value_origins", "_value_origins"):
        origins = product.get(key)
        if not isinstance(origins, dict):
            origins = {}
            product[key] = origins
        origins[value_path] = "derived"
        origins[canonical_path] = "derived"
    refs = product.get("source_refs")
    if isinstance(refs, list):
        product["source_refs"] = [
            ref
            for ref in refs
            if not (
                isinstance(ref, dict)
                and canonical_value_path(ref.get("value_path") or ref.get("field"))
                in {
                    canonical_value_path(value_path),
                    canonical_value_path(canonical_path),
                }
            )
        ]


async def _resolve_live_product(
    client: ShopifyClient,
    product: dict[str, Any],
    selected_location: dict[str, Any] | None,
) -> dict[str, Any] | None:
    handle = str(product.get("handle") or "").strip().lower()
    if not handle:
        return None
    result = await client.graphql(
        PRODUCT_BY_HANDLE_QUERY, {"query": f"handle:{handle}"}
    )
    raw_nodes = result.get("data", {}).get("products", {}).get("nodes", [])
    nodes = [
        item
        for item in raw_nodes
        if isinstance(item, dict)
        and str(item.get("handle") or "").strip().lower() == handle
    ]
    if len(nodes) != 1:
        return None
    live = nodes[0]
    connection = live.get("variants") if isinstance(live.get("variants"), dict) else {}
    variant_nodes = [
        item for item in connection.get("nodes", []) if isinstance(item, dict)
    ]
    page_info = (
        connection.get("pageInfo")
        if isinstance(connection.get("pageInfo"), dict)
        else {}
    )
    while (
        page_info.get("hasNextPage")
        and page_info.get("endCursor")
        and len(variant_nodes) <= 2048
    ):
        page_result = await client.graphql(
            PRODUCT_VARIANTS_PAGE_QUERY,
            {"id": live["id"], "after": page_info["endCursor"]},
        )
        page = page_result.get("data", {}).get("product", {}).get("variants", {})
        variant_nodes.extend(
            item for item in page.get("nodes", []) if isinstance(item, dict)
        )
        page_info = (
            page.get("pageInfo") if isinstance(page.get("pageInfo"), dict) else {}
        )
    live["variants"] = {"nodes": variant_nodes}
    if not selected_location:
        return live
    inventory_result = await client.graphql(
        INVENTORY_SNAPSHOT_QUERY,
        {"id": live["id"], "locationId": selected_location["id"]},
    )
    inventory_product = inventory_result.get("data", {}).get("product")
    if not isinstance(inventory_product, dict):
        return live
    inventory_connection = (
        inventory_product.get("variants")
        if isinstance(inventory_product.get("variants"), dict)
        else {}
    )
    inventory_nodes = [
        item for item in inventory_connection.get("nodes", []) if isinstance(item, dict)
    ]
    inventory_page_info = (
        inventory_connection.get("pageInfo")
        if isinstance(inventory_connection.get("pageInfo"), dict)
        else {}
    )
    while (
        inventory_page_info.get("hasNextPage")
        and inventory_page_info.get("endCursor")
        and len(inventory_nodes) <= 2048
    ):
        page_result = await client.graphql(
            INVENTORY_SNAPSHOT_PAGE_QUERY,
            {
                "id": live["id"],
                "locationId": selected_location["id"],
                "after": inventory_page_info["endCursor"],
            },
        )
        page = page_result.get("data", {}).get("product", {}).get("variants", {})
        inventory_nodes.extend(
            item for item in page.get("nodes", []) if isinstance(item, dict)
        )
        inventory_page_info = (
            page.get("pageInfo") if isinstance(page.get("pageInfo"), dict) else {}
        )
    inventory_product["variants"] = {"nodes": inventory_nodes}
    _apply_inventory_snapshot(live, inventory_product)
    return live


async def _repair_legacy_review_evidence(
    draft: dict[str, Any], previous: dict[str, Any] | None, tenant: str, ctx: AppContext
) -> dict[str, Any]:
    """Re-verify retained source evidence without advancing the value revision."""
    if not previous or previous.get("status") != "review":
        return draft
    if previous.get("review_contract_version") == 5:
        return draft
    file_id = str(draft.get("input_file_id") or "")
    source = ctx.supabase.file.get_file(file_id) if file_id else None
    if not isinstance(source, dict) or not isinstance(
        source.get("content"), (bytes, bytearray)
    ):
        return draft
    original_products = _products(draft)
    products = deepcopy(original_products)
    filename = str(draft.get("input_filename") or source.get("name") or file_id)
    lower_name = filename.casefold()
    if lower_name.endswith((".xlsx", ".xlsm", ".csv")):
        from application.services.spreadsheet_source_refs import (
            enrich_spreadsheet_source_refs,
        )

        enrich_spreadsheet_source_refs(
            products, bytes(source["content"]), filename, source_file_id=file_id
        )
    elif lower_name.endswith(".pdf") and ctx.services.document_layout is not None:
        from application.services.pdf_source_refs import verify_pdf_source_refs

        layout = await ctx.services.document_layout.analyze_pdf(
            bytes(source["content"]), file_id
        )
        verify_pdf_source_refs(products, layout, file_id)
    else:
        return draft
    for product in products:
        origins = (
            product.get("value_origins")
            if isinstance(product.get("value_origins"), dict)
            else {}
        )
        for ref in (
            product.get("source_refs")
            if isinstance(product.get("source_refs"), list)
            else []
        ):
            if isinstance(ref, dict) and ref.get("value_path"):
                origins.setdefault(str(ref["value_path"]), "source")
        if origins:
            product["value_origins"] = origins
    if products == original_products:
        return draft
    saved = ctx.supabase.drafts.save_product_draft(
        draft_id=str(draft["draft_id"]),
        run_id=draft.get("run_id"),
        import_mode=str(draft.get("import_mode") or "auto"),
        draft_name=draft.get("draft_name"),
        shop_domain=tenant,
        input_file_id=draft.get("input_file_id"),
        input_filename=draft.get("input_filename"),
        output_file_id=draft.get("output_file_id"),
        output_filename=draft.get("output_filename"),
        extraction_status=draft.get("extraction_status"),
        extraction_run_id=draft.get("extraction_run_id"),
        extraction_error=draft.get("extraction_error"),
        products=products,
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
    quality_report = build_quality_report(products)
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
        quality_gate_status=QUALITY_STATUS_LIVE_PENDING,
        quality_report={**quality_report, "status": QUALITY_STATUS_LIVE_PENDING},
        quality_checked_at=quality_report.get("generated_at"),
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
@router.post(
    "/product-drafts/{draft_id}/quality-gate",
    summary="Run post-extraction quality gate",
)
async def create_mutation_plan(
    draft_id: str,
    request: Request,
    payload: dict[str, Any] | None = Body(default=None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    # FastAPI parameter defaults are sentinels when route handlers are invoked
    # directly, so normalize the optional request body at the boundary.
    payload = payload if isinstance(payload, dict) else {}
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
        granted_scopes = {x.get("handle") for x in scopes if isinstance(x, dict)}
        if "write_products" not in granted_scopes:
            raise _review_error(
                "The offline session does not have write_products access",
                status_code=403,
            )
        inventory_required = _has_inventory(_products(draft))
        locations: list[dict[str, Any]] = []
        selected_location: dict[str, Any] | None = None
        auto_selected = False
        requested_location_id = payload.get("inventory_location_id")
        if requested_location_id is not None and (
            not isinstance(requested_location_id, str)
            or not requested_location_id.strip()
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_INVENTORY_LOCATION",
                    "message": "inventory_location_id must be a Shopify location ID",
                },
            )
        if inventory_required:
            missing_scopes = sorted(INVENTORY_SCOPES - granted_scopes)
            if missing_scopes:
                raise HTTPException(
                    status_code=401,
                    detail={
                        "code": SHOPIFY_REAUTH_REQUIRED,
                        "action": "/auth/login",
                        "missing_scopes": missing_scopes,
                        "message": "Reconnect Shopify to review and apply inventory quantities.",
                    },
                )
            locations_result = await client.graphql(LOCATIONS_QUERY)
            location_nodes = (
                locations_result.get("data", {}).get("locations", {}).get("nodes", [])
            )
            eligible_nodes = [
                node
                for node in location_nodes
                if isinstance(node, dict)
                and node.get("isActive") is True
                and node.get("isFulfillmentService") is False
                and node.get("id")
            ]
            locations = [_location_view(node) for node in eligible_nodes]
            eligible_by_id = {location["id"]: location for location in locations}
            if requested_location_id is not None:
                selected_location = eligible_by_id.get(requested_location_id)
                if selected_location is None:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "INVALID_INVENTORY_LOCATION",
                            "message": "Choose an active merchant-managed Shopify location.",
                        },
                    )
            else:
                prior_location = (
                    (previous or {})
                    .get("inventory_context", {})
                    .get("selected_location")
                    if isinstance((previous or {}).get("inventory_context"), dict)
                    else None
                )
                prior_id = (
                    prior_location.get("id")
                    if isinstance(prior_location, dict)
                    else None
                )
                selected_location = eligible_by_id.get(prior_id)
                if selected_location is None and len(locations) == 1:
                    selected_location = locations[0]
                    auto_selected = True
        inventory_status = (
            "not_needed"
            if not inventory_required
            else (
                "ready"
                if selected_location
                else "no_eligible_locations" if not locations else "location_required"
            )
        )
        inventory_context = {
            "required": inventory_required,
            "status": inventory_status,
            "quantity_name": "available",
            "locations": locations,
            "selected_location": selected_location,
            "auto_selected": auto_selected,
        }
        # Resolve every live product before deciding which extracted blanks are
        # truly missing. This makes the quality report describe mutation values,
        # not unsafe blank-overwrite attempts.
        persisted_products = deepcopy(_products(draft))
        inferred_any = False
        for product in persisted_products:
            inferred_any = infer_positional_option_dimensions(product) or inferred_any
        effective_products = deepcopy(persisted_products)
        live_products = [
            await _resolve_live_product(client, product, selected_location)
            for product in effective_products
        ]
        for product, live in zip(effective_products, live_products, strict=True):
            carry_forward_required_live_values(product, live)

        sku_lookup_cache: dict[str, list[dict[str, Any]]] = {}
        reserved_skus = {
            str(variant.get("sku") or "").strip().casefold()
            for product in persisted_products
            for variant in _variants(product)
            if str(variant.get("sku") or "").strip()
        }
        generated_any = inferred_any
        generated_skus: dict[tuple[int, int], str] = {}
        for product_index, (persisted, effective, live) in enumerate(
            zip(persisted_products, effective_products, live_products, strict=True)
        ):
            for variant_index, candidate in generate_missing_skus(
                effective, live
            ).items():
                folded = candidate.casefold()
                if folded in reserved_skus:
                    continue
                sku_result = await client.graphql(
                    VARIANTS_BY_SKU_QUERY, {"query": f'sku:"{candidate}"'}
                )
                raw_nodes = (
                    sku_result.get("data", {})
                    .get("productVariants", {})
                    .get("nodes", [])
                )
                exact_collision = any(
                    isinstance(node, dict)
                    and str(node.get("sku") or "").strip().casefold() == folded
                    for node in raw_nodes
                )
                sku_lookup_cache[folded] = [
                    node for node in raw_nodes if isinstance(node, dict)
                ]
                if exact_collision:
                    continue
                _mark_derived_sku(persisted, variant_index, candidate)
                _mark_derived_sku(effective, variant_index, candidate)
                reserved_skus.add(folded)
                generated_any = True
                generated_skus[(product_index, variant_index)] = candidate
        if generated_any:
            draft = _save_corrected_products(ctx, draft, persisted_products)

        quality_report = build_quality_report(
            effective_products, status=QUALITY_STATUS_LIVE_PENDING
        )
        _save_quality_state(
            ctx, draft, status=QUALITY_STATUS_LIVE_PENDING, report=quality_report
        )

        items: list[dict[str, Any]] = []
        for index, product in enumerate(effective_products):
            live = live_products[index]
            operation = "update" if live else "create"
            blockers, unapplied = validate_product(
                product, creating=operation == "create"
            )
            imported_variants = product.get("variants")
            if isinstance(imported_variants, dict):
                imported_variants = imported_variants.get("nodes")
            for variant_index, variant in enumerate(
                imported_variants if isinstance(imported_variants, list) else []
            ):
                if not isinstance(variant, dict):
                    continue
                sku = str(variant.get("sku") or "").strip()
                if not sku:
                    continue
                cache_key = sku.casefold()
                if cache_key not in sku_lookup_cache:
                    sku_result = await client.graphql(
                        VARIANTS_BY_SKU_QUERY, {"query": f'sku:"{sku}"'}
                    )
                    raw_sku_nodes = (
                        sku_result.get("data", {})
                        .get("productVariants", {})
                        .get("nodes", [])
                    )
                    sku_lookup_cache[cache_key] = [
                        node for node in raw_sku_nodes if isinstance(node, dict)
                    ]
                collisions = [
                    node
                    for node in sku_lookup_cache[cache_key]
                    if str(node.get("sku") or "").casefold() == cache_key
                    and (
                        not live
                        or not isinstance(node.get("product"), dict)
                        or node["product"].get("id") != live.get("id")
                    )
                ]
                if collisions:
                    live_issue = {
                        "issue_id": f"live_sku_collision:product-{index}-variants[{variant_index}].sku",
                        "severity": "blocker",
                        "code": "LIVE_SKU_COLLISION",
                        "product_index": index,
                        "value_path": f"variants[{variant_index}].sku",
                        "message": "This SKU is already used by another Shopify product.",
                        "remedy": "edit",
                    }
                    quality_report["issues"].append(live_issue)
                    blockers.append(
                        {
                            "code": live_issue["code"],
                            "field": live_issue["value_path"],
                            "message": live_issue["message"],
                            "remedies": ["edit", "skip"],
                        }
                    )
            product_quality_issues = issues_for_product(quality_report, index)
            existing_blocker_codes = {
                str(item.get("code")) for item in blockers if isinstance(item, dict)
            }
            blockers.extend(
                {
                    "code": str(issue.get("code")),
                    "field": issue.get("value_path"),
                    "message": issue.get("message"),
                    "remedies": [issue.get("remedy")],
                }
                for issue in product_quality_issues
                if issue.get("severity") == "blocker"
                and str(issue.get("code")) not in existing_blocker_codes
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
            for deferred_index, deferred in enumerate(unapplied):
                if not isinstance(deferred, dict):
                    continue
                deferred_issue = {
                    "issue_id": f"deferred:product-{index}-{deferred_index}-{deferred.get('field') or 'field'}",
                    "severity": "warning",
                    "code": str(deferred.get("code") or "FIELD_DEFERRED"),
                    "product_index": index,
                    "value_path": deferred.get("field"),
                    "message": "This extracted value is not supported by the current Shopify submission and will be deferred.",
                    "remedy": "review",
                }
                product_quality_issues.append(deferred_issue)
                quality_report["issues"].append(deferred_issue)
            prior_item = previous_by_source_index.get(index)
            plan_item = build_review_plan_item(
                item_id=str(uuid.uuid4()),
                currency_code=currency_code,
                source_index=index,
                source_product=product,
                live_snapshot=live,
                proposed=proposed,
                operation=operation,
                blockers=blockers,
                unapplied=unapplied,
                prior_item=prior_item,
            )
            generated_for_product = {
                sku
                for (product_index, _), sku in generated_skus.items()
                if product_index == index
            }
            for row in plan_item.get("review_changes", []):
                if (
                    isinstance(row, dict)
                    and row.get("scope") == "variant"
                    and row.get("field") == "sku"
                    and str(row.get("proposed") or "") in generated_for_product
                ):
                    row["origin"] = "derived"
                    row["source_ref"] = None
                    row["source_refs"] = []
                    row["required"] = True
                    row["selectable"] = False
                    row["included"] = True
            if operation == "create" and not str(product.get("status") or "").strip():
                for row in plan_item.get("review_changes", []):
                    if (
                        isinstance(row, dict)
                        and row.get("scope") == "product"
                        and row.get("field") == "status"
                    ):
                        row["origin"] = "defaulted"
            plan_item["quality_issues"] = product_quality_issues
            for row in plan_item.get("review_changes", []):
                if not isinstance(row, dict) or row.get("scope") != "inventory":
                    continue
                # Blank inventory means no inventory mutation. Numeric values
                # become required operations only once a location is available.
                if row.get("proposed") in (None, ""):
                    row["required"] = False
                    row["included"] = False
                    continue
                row["required"] = True
                row["selectable"] = False
                if inventory_status != "ready":
                    row["disposition"] = "deferred"
                    row["included"] = False
                    row["reason_codes"] = [
                        (
                            "NO_ELIGIBLE_INVENTORY_LOCATIONS"
                            if inventory_status == "no_eligible_locations"
                            else "INVENTORY_LOCATION_REQUIRED"
                        )
                    ]
            items.append(plan_item)
    finally:
        await client.close()
    blocked_product_count = sum(bool(item.get("blockers")) for item in items)
    blocker_count = sum(len(item.get("blockers") or []) for item in items)
    warning_count = sum(
        issue.get("severity") == "warning"
        for item in items
        for issue in item.get("quality_issues", [])
        if isinstance(issue, dict)
    )
    quality_report["summary"] = {
        "product_count": len(items),
        "valid_product_count": len(items) - blocked_product_count,
        "blocked_product_count": blocked_product_count,
        "blocker_count": blocker_count,
        "warning_count": warning_count,
    }
    plan = {
        "plan_id": str(uuid.uuid4()),
        "draft_id": draft_id,
        "shop_domain": tenant,
        "draft_revision": int(draft.get("revision") or 1),
        "version": int((previous or {}).get("version") or 0) + 1,
        "status": "review",
        "currency_code": currency_code,
        "review_contract_version": 5,
        "inventory_context": inventory_context,
        "items": items,
        "approved_at": None,
        "created_at": _now(),
        "updated_at": _now(),
        "quality_summary": quality_report.get("summary"),
    }
    plan = normalize_review_plan_rows(plan)
    review_rows = [
        row
        for item in items
        for row in item.get("review_changes", [])
        if isinstance(row, dict)
    ]
    unknown_count = sum(row.get("origin") == "unknown" for row in review_rows)
    LOG.info(
        "import_review_plan_built draft_id=%s version=%s rows=%s unknown_provenance=%s",
        draft_id,
        plan["version"],
        len(review_rows),
        unknown_count,
    )
    ready_report = {**quality_report, "status": QUALITY_STATUS_READY}
    _service(ctx).save_mutation_plan(plan)
    _save_quality_state(ctx, draft, status=QUALITY_STATUS_READY, report=ready_report)
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
        if plan.get("review_contract_version") not in {2, 3, 4, 5}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": UNSUPPORTED_REVIEW_CONTRACT,
                    "message": "Regenerate this review plan before selecting fields",
                },
            )
        if not isinstance(item_selections, list) or not all(
            isinstance(x, dict) for x in item_selections
        ):
            raise HTTPException(
                status_code=400, detail="item_selections must be an array of objects"
            )
        selections = {
            str(entry.get("item_id") or ""): entry for entry in item_selections
        }
        valid_ids = {str(item.get("item_id")) for item in items}
        if set(selections) - valid_ids:
            raise _review_error("Selection contains an unknown product")
        for item in items:
            entry = selections.get(str(item.get("item_id")))
            if entry is None:
                continue
            included = entry.get("included")
            change_ids = entry.get("included_change_ids")
            if (
                not isinstance(included, bool)
                or not isinstance(change_ids, list)
                or not all(isinstance(x, str) for x in change_ids)
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Each item selection requires included and included_change_ids",
                )
            if included and not item.get("selectable"):
                raise _review_error(
                    "Selection contains a product that cannot be applied"
                )
            rows = [
                row for row in item.get("review_changes", []) if isinstance(row, dict)
            ]
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
                raise _review_error(
                    "Required or structurally dependent changes cannot be excluded"
                )
            if requested_change_ids & disallowed:
                raise _review_error("Blocked or deferred changes cannot be included")
            item["selected"] = bool(included)
            item["selection_intent"] = "include" if included else "exclude"
            for row in rows:
                row["included"] = bool(
                    included
                    and row.get("disposition") == "applicable"
                    and str(row.get("change_id")) in requested_change_ids
                )
            item["review_changes"] = rows
    else:
        requested = payload.get("selected_item_ids")
        if not isinstance(requested, list) or not all(
            isinstance(x, str) for x in requested
        ):
            raise HTTPException(
                status_code=400, detail="selected_item_ids must be an array of strings"
            )
        selected_ids = set(requested)
        valid_selectable = {str(x.get("item_id")) for x in items if x.get("selectable")}
        if selected_ids - valid_selectable:
            raise _review_error("Selection contains an item that cannot be applied")
        for item in items:
            selected = bool(
                item.get("selectable") and item.get("item_id") in selected_ids
            )
            item["selected"] = selected
            item["selection_intent"] = "include" if selected else "exclude"
            for row in item.get("review_changes", []):
                if isinstance(row, dict):
                    row["included"] = bool(
                        selected and row.get("disposition") == "applicable"
                    )
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
    inventory_context = plan.get("inventory_context")
    if (
        plan.get("review_contract_version") in {4, 5}
        and isinstance(inventory_context, dict)
        and inventory_context.get("required")
    ):
        if inventory_context.get("status") != "ready":
            raise _review_error(
                "Choose an inventory location before applying inventory"
            )
        for item in selected:
            rows = [
                row for row in item.get("review_changes", []) if isinstance(row, dict)
            ]
            inventory_rows = [row for row in rows if row.get("scope") == "inventory"]
            if any(
                not row.get("included") or row.get("disposition") != "applicable"
                for row in inventory_rows
            ):
                raise _review_error("All inventory and tracking changes are required")
    selected_warnings = sorted(
        str(issue.get("issue_id") or issue.get("code") or "")
        for item in selected
        for issue in item.get("quality_issues", [])
        if isinstance(issue, dict) and issue.get("severity") == "warning"
    )
    if selected_warnings and payload.get("warnings_acknowledged") is not True:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "QUALITY_WARNINGS_ACKNOWLEDGMENT_REQUIRED",
                "message": "Review and acknowledge the remaining warnings before applying changes.",
                "warning_count": len(selected_warnings),
            },
        )
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
                "execution_proposed": (
                    materialize_execution_proposed(item)
                    if item.get("selected")
                    else None
                ),
                "inventory_operations": (
                    materialize_inventory_operations(plan, item)
                    if item.get("selected")
                    else []
                ),
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
        "warnings_acknowledged_at": _now() if selected_warnings else None,
        "warnings_acknowledgment_hash": (
            hashlib.sha256(json.dumps(selected_warnings).encode("utf-8")).hexdigest()
            if selected_warnings
            else None
        ),
        "updated_at": _now(),
    }
    _service(ctx).save_mutation_plan(approved)
    return {"plan": approved}
