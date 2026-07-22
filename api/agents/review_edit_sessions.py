"""Recoverable, tenant-scoped editing for the consolidated import review."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import uuid
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from ai.excel_writer import create_excel_bytes
from ai.excel_reader import WorkbookValidationError, read_generated_workbook
from ai.models import ProductsList
from app_context import AppContext, get_ctx
from application.use_cases.files.save_file import execute as save_file
from application.domain.shopify_mutation_plan import _normalized_path_part, _schema_from_product, _variant_subject, _variants
from .mutation_plans import REVIEW_REQUIRED, UNSUPPORTED_REVIEW_CONTRACT, create_mutation_plan
from .utils import require_authenticated_shop_domain

router = APIRouter()
LOG = logging.getLogger(__name__)


def _service(ctx: AppContext) -> Any:
    return getattr(ctx.supabase, "_service", ctx.supabase)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": REVIEW_REQUIRED, "message": message})


def _products(draft: dict[str, Any]) -> list[dict[str, Any]]:
    value = draft.get("products")
    return deepcopy([item for item in value if isinstance(item, dict)]) if isinstance(value, list) else []


def _require_review_v2(ctx: AppContext, draft_id: str, tenant: str) -> dict[str, Any] | None:
    plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if isinstance(plan, dict) and plan.get("review_contract_version") != 2:
        raise HTTPException(
            status_code=409,
            detail={"code": UNSUPPORTED_REVIEW_CONTRACT, "message": "Regenerate this review plan before editing"},
        )
    return plan


def _workbook_model(products: list[dict[str, Any]]) -> ProductsList:
    normalized = deepcopy(products)
    for product in normalized:
        if isinstance(product.get("_value_origins"), dict) and not isinstance(product.get("value_origins"), dict):
            product["value_origins"] = product["_value_origins"]
    return ProductsList.model_validate({"products": normalized})


def _mark_workbook_edits(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    field_paths = {
        "title": "title",
        "body_html": "descriptionHtml",
        "vendor": "vendor",
        "product_type": "productType",
        "product_category": "productType",
        "seo_title": "seo.title",
        "seo_description": "seo.description",
        "tags": "tags",
        "status": "status",
    }
    for index, product in enumerate(after):
        original = before[index] if index < len(before) else {}
        origins = product.get("value_origins")
        if not isinstance(origins, dict):
            origins = {}
            product["value_origins"] = origins
        for field, path in field_paths.items():
            if product.get(field) != original.get(field):
                origins[path] = "merchant_edited"
        option_fields = ("name", "values")
        original_options = original.get("options") if isinstance(original.get("options"), list) else []
        for option_index, option in enumerate(product.get("options") if isinstance(product.get("options"), list) else []):
            old_option = original_options[option_index] if option_index < len(original_options) and isinstance(original_options[option_index], dict) else {}
            if not isinstance(option, dict):
                continue
            subject = option.get("name") or old_option.get("name") or f"Option {option_index + 1}"
            for field in option_fields:
                if option.get(field) != old_option.get(field):
                    origins[f"option[{_normalized_path_part(subject)}].{field}"] = "merchant_edited"
        option_names = _schema_from_product(product)
        old_variants = _variants(original)
        variant_aliases = {
            "compare_at_price": "compareAtPrice", "inventory_policy": "inventoryPolicy",
            "tax_code": "taxCode", "cost_per_item": "inventoryItem.cost",
            "tracked": "inventoryItem.tracked", "requires_shipping": "inventoryItem.requiresShipping",
            "weight": "inventoryItem.weight.value", "weight_unit": "inventoryItem.weight.unit",
        }
        for variant_index, variant in enumerate(_variants(product)):
            old_variant = old_variants[variant_index] if variant_index < len(old_variants) else {}
            subject = _variant_subject(variant, option_names)
            for field in ("price", "compare_at_price", "sku", "barcode", "taxable", "tax_code", "inventory_policy", "cost_per_item", "tracked", "requires_shipping", "weight", "weight_unit"):
                if variant.get(field) != old_variant.get(field):
                    canonical = variant_aliases.get(field, field)
                    scope = "inventory" if canonical.startswith("inventoryItem.") else "variant"
                    origins[f"{scope}[{_normalized_path_part(subject)}].{canonical}"] = "merchant_edited"
        old_metafields = original.get("metafields") if isinstance(original.get("metafields"), list) else []
        for mf_index, metafield in enumerate(product.get("metafields") if isinstance(product.get("metafields"), list) else []):
            old_metafield = old_metafields[mf_index] if mf_index < len(old_metafields) and isinstance(old_metafields[mf_index], dict) else {}
            if not isinstance(metafield, dict):
                continue
            subject = f"{metafield.get('namespace')}.{metafield.get('key')}"
            for field in ("value", "type"):
                if metafield.get(field) != old_metafield.get(field):
                    origins[f"metafield[{_normalized_path_part(subject)}].{field}"] = "merchant_edited"
        if isinstance(original.get("source_refs"), list):
            product["source_refs"] = deepcopy(original["source_refs"])
    return after


def _coerce_review_value(row: dict[str, Any], value: Any) -> Any:
    value_type = row.get("value_type")
    if value_type == "boolean" and isinstance(value, str):
        if value.strip().casefold() in {"true", "yes", "1", "on"}:
            return True
        if value.strip().casefold() in {"false", "no", "0", "off"}:
            return False
    if value_type in {"money", "weight"} and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if value_type in {"json", "metafield"} and isinstance(value, str):
        try:
            return __import__("json").loads(value)
        except ValueError:
            return value
    return value


def _set_product_value(product: dict[str, Any], row: dict[str, Any], value: Any) -> None:
    path = str(row.get("value_path") or "")
    value = _coerce_review_value(row, value)
    aliases = {
        "descriptionHtml": "body_html",
        "productType": "product_type",
        "seo.title": "seo_title",
        "seo.description": "seo_description",
    }
    resolved = aliases.get(path, path)
    scope = str(row.get("scope") or "product")
    field = str(row.get("field") or "")
    subject = str(row.get("subject") or "")
    if scope == "product":
        if "." in resolved or "[" in resolved:
            raise HTTPException(status_code=422, detail=f"Grid editing is not supported for {path}")
        product[resolved] = value
    elif scope in {"variant", "inventory"}:
        option_names = _schema_from_product(product)
        target = next(
            (variant for variant in _variants(product) if _variant_subject(variant, option_names) == subject),
            None,
        )
        if target is None:
            raise HTTPException(status_code=422, detail=f"Variant is unavailable for {path}")
        aliases = {
            "compareAtPrice": "compare_at_price",
            "inventoryPolicy": "inventory_policy",
            "taxCode": "tax_code",
            "inventoryItem.cost": "cost_per_item",
            "inventoryItem.tracked": "tracked",
            "inventoryItem.requiresShipping": "requires_shipping",
            "inventoryItem.weight.value": "weight",
            "inventoryItem.weight.unit": "weight_unit",
        }
        target[aliases.get(field, field)] = value
    elif scope == "option":
        options = product.get("options") if isinstance(product.get("options"), list) else []
        target = next((option for option in options if isinstance(option, dict) and str(option.get("name") or "") == subject), None)
        if target is None:
            raise HTTPException(status_code=422, detail=f"Option is unavailable for {path}")
        target[field] = value
    elif scope == "metafield":
        namespace, _, key = subject.partition(".")
        metafields = product.get("metafields") if isinstance(product.get("metafields"), list) else []
        target = next((item for item in metafields if isinstance(item, dict) and item.get("namespace") == namespace and item.get("key") == key), None)
        if target is None:
            raise HTTPException(status_code=422, detail=f"Metafield is unavailable for {path}")
        target[field] = value
    else:
        raise HTTPException(status_code=422, detail=f"Grid editing is not supported for {path}")
    origins = product.get("_value_origins")
    if not isinstance(origins, dict):
        origins = {}
        product["_value_origins"] = origins
    origins[path] = "merchant_edited"


def _apply_grid_changes(
    products: list[dict[str, Any]], plan: dict[str, Any], changes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: dict[str, tuple[int, dict[str, Any]]] = {}
    for item in plan.get("items", []):
        if not isinstance(item, dict) or not isinstance(item.get("source_index"), int):
            continue
        for row in item.get("review_changes", []):
            if isinstance(row, dict) and row.get("change_id"):
                rows[str(row["change_id"])] = (int(item["source_index"]), row)
    for change in changes:
        if not isinstance(change, dict):
            raise HTTPException(status_code=400, detail="changes must contain objects")
        change_id = str(change.get("change_id") or "")
        match = rows.get(change_id)
        if not match:
            raise HTTPException(status_code=422, detail=f"Unknown change_id: {change_id}")
        product_index, row = match
        path = str(change.get("value_path") or "")
        if path != row.get("value_path"):
            raise HTTPException(status_code=422, detail=f"value_path does not match {change_id}")
        if product_index >= len(products):
            raise HTTPException(status_code=422, detail="Change targets a missing product")
        _set_product_value(products[product_index], row, change.get("value"))
    return products


@router.get("/product-drafts/{draft_id}/review-edit-sessions")
async def get_active_review_edit_session(
    draft_id: str, request: Request, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request)
    session = _service(ctx).get_active_review_edit_session(draft_id, shop_domain=tenant)
    return {"session": session}


@router.post("/product-drafts/{draft_id}/review-edit-sessions")
async def create_review_edit_session(
    draft_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    _require_review_v2(ctx, draft_id, tenant)
    surface = payload.get("surface")
    if surface not in {"grid", "workbook"}:
        raise HTTPException(status_code=400, detail="surface must be grid or workbook")
    draft = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=tenant)
    if not isinstance(draft, dict):
        raise HTTPException(status_code=404, detail="Draft not found")
    base_revision = payload.get("base_revision")
    current_revision = int(draft.get("revision") or 1)
    if not isinstance(base_revision, int) or base_revision != current_revision:
        raise _conflict("Draft revision is stale")
    active = _service(ctx).get_active_review_edit_session(draft_id, shop_domain=tenant)
    if active:
        if active.get("surface") == surface and int(active.get("base_draft_revision") or 0) == current_revision:
            return {"session": active, "resumed": True}
        raise _conflict("Apply or discard the active edit session before switching surfaces")

    products = _products(draft)
    working_file_id: str | None = None
    if surface == "workbook":
        working_file_id = str(uuid.uuid4())
        workbook = create_excel_bytes(_workbook_model(products))
        save_file(
            supabase=ctx.supabase,
            file_id=working_file_id,
            name=f"working-{draft.get('output_filename') or 'generated-import.xlsx'}",
            content=workbook,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            file_origin="review_working_copy",
            shop_domain=tenant,
        )
    now = _now()
    session = _service(ctx).save_review_edit_session({
        "session_id": str(uuid.uuid4()),
        "draft_id": draft_id,
        "shop_domain": tenant,
        "surface": surface,
        "base_draft_revision": current_revision,
        "edit_revision": 1,
        "pending_products": products,
        "working_file_id": working_file_id,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    })
    return {"session": session, "resumed": False}


@router.patch("/product-drafts/{draft_id}/review-edit-sessions/{session_id}")
async def patch_review_edit_session(
    draft_id: str,
    session_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    session = _service(ctx).get_review_edit_session(session_id, shop_domain=tenant)
    if not session or session.get("draft_id") != draft_id:
        raise HTTPException(status_code=404, detail="Edit session not found")
    if session.get("status") != "active" or session.get("surface") != "grid":
        raise _conflict("Grid edit session is not active")
    expected = payload.get("expected_edit_revision")
    current = int(session.get("edit_revision") or 1)
    if not isinstance(expected, int) or expected != current:
        raise _conflict("Edit revision is stale")
    changes = payload.get("changes")
    if not isinstance(changes, list):
        raise HTTPException(status_code=400, detail="changes must be an array")
    plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if not isinstance(plan, dict):
        raise _conflict("Mutation plan is missing")
    if plan.get("review_contract_version") != 2:
        raise HTTPException(status_code=409, detail={"code": UNSUPPORTED_REVIEW_CONTRACT, "message": "Regenerate this review plan before editing"})
    pending = _apply_grid_changes(_products({"products": session.get("pending_products")}), plan, changes)
    saved = _service(ctx).save_review_edit_session({
        **session,
        "pending_products": pending,
        "edit_revision": current + 1,
        "updated_at": _now(),
    })
    return {"session": saved}


@router.post("/product-drafts/{draft_id}/review-edit-sessions/{session_id}/apply")
async def apply_review_edit_session(
    draft_id: str,
    session_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default={}),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    session = _service(ctx).get_review_edit_session(session_id, shop_domain=tenant)
    if not session or session.get("draft_id") != draft_id:
        raise HTTPException(status_code=404, detail="Edit session not found")
    if session.get("status") != "active":
        raise _conflict("Edit session is not active")
    expected = payload.get("expected_edit_revision")
    if expected is not None and expected != int(session.get("edit_revision") or 1):
        raise _conflict("Edit revision is stale")
    draft = ctx.supabase.drafts.get_product_draft(draft_id, shop_domain=tenant)
    if not draft or int(draft.get("revision") or 1) != int(session.get("base_draft_revision") or 0):
        raise _conflict("Draft revision is stale")
    pending_before = _products({"products": session.get("pending_products")})
    if session.get("surface") == "workbook":
        working_file_id = str(session.get("working_file_id") or "")
        working_file = ctx.supabase.file.get_file(working_file_id)
        if not working_file or str(working_file.get("shop_domain") or "").strip().lower() != tenant:
            raise _conflict("Workbook working copy is unavailable")
        try:
            parsed = read_generated_workbook(bytes(working_file["content"]))
        except WorkbookValidationError as exc:
            LOG.warning(
                "import_review_workbook_validation_failed draft_id=%s session_id=%s errors=%s",
                draft_id,
                session_id,
                len(exc.errors),
            )
            raise HTTPException(
                status_code=422,
                detail={"code": "WORKBOOK_VALIDATION_FAILED", "errors": exc.errors},
            ) from exc
        products = _mark_workbook_edits(pending_before, parsed)
    else:
        products = pending_before
    new_revision = int(draft.get("revision") or 1) + 1
    output_file_id = str(draft.get("output_file_id") or uuid.uuid4())
    output_filename = str(draft.get("output_filename") or "generated-import.xlsx")
    workbook = create_excel_bytes(_workbook_model(products))
    save_file(
        supabase=ctx.supabase,
        file_id=output_file_id,
        name=output_filename,
        content=workbook,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_origin="workflow_output",
        shop_domain=tenant,
    )
    saved_draft = ctx.supabase.drafts.save_product_draft(
        draft_id=draft_id,
        run_id=draft.get("run_id"),
        import_mode=str(draft.get("import_mode") or "auto"),
        draft_name=draft.get("draft_name"),
        shop_domain=tenant,
        input_file_id=draft.get("input_file_id"),
        input_filename=draft.get("input_filename"),
        output_file_id=output_file_id,
        output_filename=output_filename,
        extraction_status=draft.get("extraction_status"),
        extraction_run_id=draft.get("extraction_run_id"),
        extraction_error=draft.get("extraction_error"),
        products=products,
        revision=new_revision,
    )
    plan_response = await create_mutation_plan(draft_id=draft_id, request=request, ctx=ctx)
    applied = _service(ctx).save_review_edit_session({**session, "status": "applied", "updated_at": _now()})
    if isinstance(session.get("working_file_id"), str) and session["working_file_id"]:
        ctx.supabase.file.delete_file(session["working_file_id"])
    canonical_file = ctx.supabase.file.get_file(output_file_id) or {}
    return {
        "session": applied,
        "draft": saved_draft,
        "workbook_revision": canonical_file.get("content_revision") or new_revision,
        "plan": plan_response["plan"],
    }


@router.delete("/product-drafts/{draft_id}/review-edit-sessions/{session_id}")
async def discard_review_edit_session(
    draft_id: str,
    session_id: str,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request)
    session = _service(ctx).get_review_edit_session(session_id, shop_domain=tenant)
    if not session or session.get("draft_id") != draft_id:
        raise HTTPException(status_code=404, detail="Edit session not found")
    if session.get("status") == "active":
        session = _service(ctx).save_review_edit_session({**session, "status": "discarded", "updated_at": _now()})
        if isinstance(session.get("working_file_id"), str) and session["working_file_id"]:
            ctx.supabase.file.delete_file(session["working_file_id"])
    return {"session": session}
