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


def _set_product_value(
    product: dict[str, Any],
    row: dict[str, Any],
    value: Any,
    *,
    origin: str = "merchant_edited",
) -> None:
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
    origin_maps = [
        candidate
        for candidate in (product.get("value_origins"), product.get("_value_origins"))
        if isinstance(candidate, dict)
    ]
    if not origin_maps:
        origin_maps = [{}]
        product["_value_origins"] = origin_maps[0]
    for origins in origin_maps:
        origins[path] = origin


def _snapshot_working_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Freeze the proposal and provenance that per-value revert must restore."""
    working_plan = deepcopy(plan)
    for item in working_plan.get("items", []):
        if not isinstance(item, dict):
            continue
        for row in item.get("review_changes", []):
            if not isinstance(row, dict):
                continue
            row.setdefault("original_proposed", deepcopy(row.get("proposed")))
            row.setdefault("original_origin", row.get("origin", "unknown"))
            row.setdefault(
                "original_source_ref",
                deepcopy(row.get("source_ref"))
                if isinstance(row.get("source_ref"), dict)
                else None,
            )
    return working_plan


def _working_rows(
    plan: dict[str, Any],
) -> dict[str, tuple[int, dict[str, Any]]]:
    rows: dict[str, tuple[int, dict[str, Any]]] = {}
    for item in plan.get("items", []):
        if not isinstance(item, dict) or not isinstance(item.get("source_index"), int):
            continue
        for row in item.get("review_changes", []):
            if isinstance(row, dict) and row.get("change_id"):
                rows[str(row["change_id"])] = (int(item["source_index"]), row)
    return rows


def _working_items(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["item_id"]): item
        for item in plan.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }


def _selection_state(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected": bool(item.get("selected")),
        "included_change_ids": [
            str(row["change_id"])
            for row in item.get("review_changes", [])
            if isinstance(row, dict) and row.get("change_id") and row.get("included")
        ],
    }


def _set_selection_state(item: dict[str, Any], state: dict[str, Any]) -> None:
    included_ids = set(state.get("included_change_ids") or [])
    item["selected"] = bool(state.get("selected"))
    for row in item.get("review_changes", []):
        if isinstance(row, dict) and row.get("change_id"):
            row["included"] = str(row["change_id"]) in included_ids


def _preserve_working_selection(
    rebuilt_plan: dict[str, Any], working_plan: dict[str, Any]
) -> dict[str, Any]:
    """Carry durable grid inclusion choices into a newly generated plan.

    Plan generation assigns new item ids, so source_index and stable change ids
    are the only identities that can safely bridge the old and new plans.
    """
    preserved_by_source_index = {
        int(item["source_index"]): _selection_state(item)
        for item in working_plan.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("source_index"), int)
    }
    result = deepcopy(rebuilt_plan)
    for item in result.get("items", []):
        if not isinstance(item, dict) or not isinstance(item.get("source_index"), int):
            continue
        preserved = preserved_by_source_index.get(int(item["source_index"]))
        if preserved is None:
            continue
        item["selected"] = bool(item.get("selectable") and preserved["selected"])
        included_ids = set(preserved["included_change_ids"])
        for row in item.get("review_changes", []):
            if not isinstance(row, dict):
                continue
            row["included"] = bool(
                item["selected"]
                and row.get("disposition") == "applicable"
                and (row.get("required") or str(row.get("change_id") or "") in included_ids)
            )
    return result


def _validate_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HTTPException(status_code=400, detail=f"{field} must be an array of strings")
    return list(dict.fromkeys(value))


def _validated_pages(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="pagesByItem must be an object")
    if any(
        not isinstance(key, str)
        or not isinstance(page, int)
        or isinstance(page, bool)
        or page < 0
        for key, page in value.items()
    ):
        raise HTTPException(
            status_code=400,
            detail="pagesByItem values must be non-negative integers",
        )
    return deepcopy(value)


def _history_entry(
    operation: str,
    before: Any,
    after: Any,
    **identity: Any,
) -> dict[str, Any]:
    return {
        "operation_id": str(uuid.uuid4()),
        "operation": operation,
        **identity,
        "before": deepcopy(before),
        "after": deepcopy(after),
        "created_at": _now(),
    }


def _operation_payloads(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Return durable operations, or None when the legacy changes contract is used."""
    if "operation" in payload and "operations" in payload:
        raise HTTPException(status_code=400, detail="Use operation or operations, not both")
    raw: Any
    if "operation" in payload:
        raw = payload["operation"]
        if isinstance(raw, str):
            return [{**payload, "operation": raw}]
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="operation must be an object")
        return [raw]
    if "operations" in payload:
        raw = payload["operations"]
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise HTTPException(status_code=400, detail="operations must be an array of objects")
        return raw
    return None


def _apply_value_operations(
    products: list[dict[str, Any]],
    working_plan: dict[str, Any],
    operations: list[dict[str, Any]],
    history: list[dict[str, Any]],
    history_cursor: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    rows = _working_rows(working_plan)
    appended: list[dict[str, Any]] = []
    for operation in operations:
        operation_type = operation.get("operation") or operation.get("type")
        if operation_type not in {"value_edit", "value_revert"}:
            raise HTTPException(status_code=400, detail="operation must be value_edit or value_revert")
        change_id = str(operation.get("change_id") or "")
        match = rows.get(change_id)
        if not match:
            raise HTTPException(status_code=422, detail=f"Unknown change_id: {change_id}")
        product_index, row = match
        path = str(operation.get("value_path") or "")
        if path != row.get("value_path"):
            raise HTTPException(status_code=422, detail=f"value_path does not match {change_id}")
        if product_index >= len(products):
            raise HTTPException(status_code=422, detail="Change targets a missing product")

        before = {
            "proposed": deepcopy(row.get("proposed")),
            "origin": row.get("origin", "unknown"),
            "source_ref": deepcopy(row.get("source_ref"))
            if isinstance(row.get("source_ref"), dict)
            else None,
        }
        if operation_type == "value_revert":
            value = deepcopy(row.get("original_proposed"))
            origin = str(row.get("original_origin") or "unknown")
            source_ref = row.get("original_source_ref")
        else:
            value = operation.get("value")
            origin = "merchant_edited"
            source_ref = None

        _set_product_value(products[product_index], row, value, origin=origin)
        row["proposed"] = deepcopy(_coerce_review_value(row, value))
        row["origin"] = origin
        if isinstance(source_ref, dict):
            row["source_ref"] = deepcopy(source_ref)
        else:
            row.pop("source_ref", None)
        after = {
            "proposed": deepcopy(row.get("proposed")),
            "origin": row["origin"],
            "source_ref": deepcopy(row.get("source_ref"))
            if isinstance(row.get("source_ref"), dict)
            else None,
        }
        appended.append(_history_entry(
            str(operation_type),
            before,
            after,
            change_id=change_id,
            value_path=path,
        ))

    # A fresh operation after undo replaces the abandoned redo branch.
    durable_history = deepcopy(history[:history_cursor])
    durable_history.extend(appended)
    return products, durable_history, len(durable_history)


def _apply_ui_operation(
    working_plan: dict[str, Any],
    grid_state: dict[str, Any],
    operation: dict[str, Any],
) -> dict[str, Any]:
    operation_type = operation.get("operation") or operation.get("type")
    if operation_type == "selection":
        item_id = str(operation.get("item_id") or "")
        item = _working_items(working_plan).get(item_id)
        if item is None:
            raise HTTPException(status_code=422, detail=f"Unknown item_id: {item_id}")
        selected = operation.get("selected")
        if not isinstance(selected, bool):
            raise HTTPException(status_code=400, detail="selected must be a boolean")
        included_ids = _validate_string_list(
            operation.get("included_change_ids"), "included_change_ids"
        )
        valid_ids = {
            str(row["change_id"])
            for row in item.get("review_changes", [])
            if isinstance(row, dict) and row.get("change_id")
        }
        unknown = set(included_ids) - valid_ids
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown included change_id: {sorted(unknown)[0]}",
            )
        if not selected and included_ids:
            raise HTTPException(
                status_code=422,
                detail="A deselected item cannot include changes",
            )
        if selected and item.get("selectable") is False:
            raise HTTPException(status_code=422, detail=f"Item is not selectable: {item_id}")
        before = _selection_state(item)
        after = {"selected": selected, "included_change_ids": included_ids}
        _set_selection_state(item, after)
        return _history_entry(
            "selection", before, after, item_id=item_id
        )

    if operation_type == "filters":
        value = operation.get("filterValues")
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail="filterValues must be an object")
        before = deepcopy(grid_state.get("filterValues") or {})
        after = deepcopy(value)
        grid_state["filterValues"] = after
        return _history_entry("filters", before, after)

    if operation_type == "expansion":
        after = _validate_string_list(
            operation.get("expandedItemIds"), "expandedItemIds"
        )
        before = deepcopy(grid_state.get("expandedItemIds") or [])
        grid_state["expandedItemIds"] = after
        return _history_entry("expansion", before, after)

    if operation_type == "pagination":
        after = _validated_pages(operation.get("pagesByItem"))
        before = deepcopy(grid_state.get("pagesByItem") or {})
        grid_state["pagesByItem"] = after
        return _history_entry("pagination", before, after)

    if operation_type == "workspace_view":
        after = operation.get("workspaceView")
        if after not in {"split", "changes", "spreadsheet"}:
            raise HTTPException(
                status_code=400,
                detail="workspaceView must be split, changes, or spreadsheet",
            )
        before = str(grid_state.get("workspaceView") or "split")
        grid_state["workspaceView"] = after
        return _history_entry("workspace_view", before, after)

    raise HTTPException(
        status_code=400,
        detail=(
            "operation must be value_edit, value_revert, selection, filters, "
            "expansion, pagination, or workspace_view"
        ),
    )


def _set_row_history_state(
    products: list[dict[str, Any]],
    working_plan: dict[str, Any],
    entry: dict[str, Any],
    state: dict[str, Any],
) -> None:
    change_id = str(entry.get("change_id") or "")
    match = _working_rows(working_plan).get(change_id)
    if match is None:
        raise HTTPException(status_code=409, detail=f"History row is unavailable: {change_id}")
    product_index, row = match
    if product_index >= len(products):
        raise HTTPException(status_code=409, detail="History product is unavailable")
    proposed = deepcopy(state.get("proposed"))
    origin = str(state.get("origin") or "unknown")
    _set_product_value(products[product_index], row, proposed, origin=origin)
    row["proposed"] = proposed
    row["origin"] = origin
    source_ref = state.get("source_ref")
    if isinstance(source_ref, dict):
        row["source_ref"] = deepcopy(source_ref)
    else:
        row.pop("source_ref", None)


def _replay_history_entry(
    products: list[dict[str, Any]],
    working_plan: dict[str, Any],
    grid_state: dict[str, Any],
    entry: dict[str, Any],
    *,
    direction: str,
) -> None:
    state = entry.get(direction)
    if not isinstance(state, (dict, list, str)):
        raise HTTPException(status_code=409, detail="History entry is not replayable")
    operation = entry.get("operation")
    if operation in {"value_edit", "value_revert"}:
        if not isinstance(state, dict):
            raise HTTPException(status_code=409, detail="Value history is invalid")
        _set_row_history_state(products, working_plan, entry, state)
    elif operation == "selection":
        item = _working_items(working_plan).get(str(entry.get("item_id") or ""))
        if item is None or not isinstance(state, dict):
            raise HTTPException(status_code=409, detail="Selection history is invalid")
        _set_selection_state(item, state)
    elif operation == "filters":
        if not isinstance(state, dict):
            raise HTTPException(status_code=409, detail="Filter history is invalid")
        grid_state["filterValues"] = deepcopy(state)
    elif operation == "expansion":
        if not isinstance(state, list):
            raise HTTPException(status_code=409, detail="Expansion history is invalid")
        grid_state["expandedItemIds"] = deepcopy(state)
    elif operation == "pagination":
        if not isinstance(state, dict):
            raise HTTPException(status_code=409, detail="Pagination history is invalid")
        grid_state["pagesByItem"] = deepcopy(state)
    elif operation == "workspace_view":
        if not isinstance(state, str):
            raise HTTPException(status_code=409, detail="Workspace history is invalid")
        grid_state["workspaceView"] = state
    else:
        raise HTTPException(status_code=409, detail="History operation is unsupported")


def _apply_operations(
    products: list[dict[str, Any]],
    working_plan: dict[str, Any],
    grid_state: dict[str, Any],
    operations: list[dict[str, Any]],
    history: list[dict[str, Any]],
    history_cursor: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    durable_history = deepcopy(history[:history_cursor])
    for operation in operations:
        operation_type = operation.get("operation") or operation.get("type")
        if operation_type in {"value_edit", "value_revert"}:
            products, durable_history, history_cursor = _apply_value_operations(
                products,
                working_plan,
                [operation],
                durable_history,
                len(durable_history),
            )
        else:
            durable_history.append(
                _apply_ui_operation(working_plan, grid_state, operation)
            )
            history_cursor = len(durable_history)
    return products, durable_history, history_cursor


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
    plan = _require_review_v2(ctx, draft_id, tenant)
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
        "working_plan": _snapshot_working_plan(plan) if isinstance(plan, dict) else {},
        "base_plan_id": plan.get("plan_id") if isinstance(plan, dict) else None,
        "grid_state": {
            "filterValues": {"changes": "hide_deferred"},
            "expandedItemIds": [],
            "pagesByItem": {},
            "workspaceView": "split",
        },
        "history": [],
        "history_cursor": 0,
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
    latest_plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if not isinstance(latest_plan, dict):
        raise _conflict("Mutation plan is missing")
    if latest_plan.get("review_contract_version") != 2:
        raise HTTPException(status_code=409, detail={"code": UNSUPPORTED_REVIEW_CONTRACT, "message": "Regenerate this review plan before editing"})
    working_plan = session.get("working_plan")
    if not isinstance(working_plan, dict) or not working_plan.get("items"):
        working_plan = _snapshot_working_plan(latest_plan)
    else:
        working_plan = _snapshot_working_plan(working_plan)
    pending = _products({"products": session.get("pending_products")})
    operations = _operation_payloads(payload)
    history = session.get("history") if isinstance(session.get("history"), list) else []
    history_cursor = session.get("history_cursor")
    if not isinstance(history_cursor, int) or isinstance(history_cursor, bool):
        history_cursor = len(history)
    history_cursor = max(0, min(history_cursor, len(history)))
    grid_state = deepcopy(session.get("grid_state"))
    if not isinstance(grid_state, dict):
        grid_state = {
            "filterValues": {"changes": "hide_deferred"},
            "expandedItemIds": [],
            "pagesByItem": {},
            "workspaceView": "split",
        }
    if operations is None:
        changes = payload.get("changes")
        if not isinstance(changes, list):
            raise HTTPException(status_code=400, detail="changes must be an array")
        operations = [
            {**change, "operation": "value_edit"}
            if isinstance(change, dict)
            else change
            for change in changes
        ]
    if any(not isinstance(operation, dict) for operation in operations):
        raise HTTPException(status_code=400, detail="changes must contain objects")
    pending, history, history_cursor = _apply_operations(
        pending,
        working_plan,
        grid_state,
        operations,
        history,
        history_cursor,
    )
    saved = _service(ctx).save_review_edit_session({
        **session,
        "pending_products": pending,
        "working_plan": working_plan,
        "grid_state": grid_state,
        "history": history,
        "history_cursor": history_cursor,
        "edit_revision": current + 1,
        "updated_at": _now(),
    })
    return {"session": saved}


def _grid_session_for_action(
    draft_id: str,
    session_id: str,
    tenant: str,
    payload: dict[str, Any],
    ctx: AppContext,
) -> tuple[dict[str, Any], int]:
    session = _service(ctx).get_review_edit_session(session_id, shop_domain=tenant)
    if not session or session.get("draft_id") != draft_id:
        raise HTTPException(status_code=404, detail="Edit session not found")
    if session.get("status") != "active" or session.get("surface") != "grid":
        raise _conflict("Grid edit session is not active")
    current = int(session.get("edit_revision") or 1)
    expected = payload.get("expected_edit_revision")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected != current:
        raise _conflict("Edit revision is stale")
    return session, current


def _save_history_action(
    session: dict[str, Any],
    current: int,
    ctx: AppContext,
    *,
    cursor: int,
    history: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    working_plan: dict[str, Any],
    grid_state: dict[str, Any],
) -> dict[str, Any]:
    return _service(ctx).save_review_edit_session({
        **session,
        "pending_products": pending,
        "working_plan": working_plan,
        "grid_state": grid_state,
        "history": history,
        "history_cursor": cursor,
        "edit_revision": current + 1,
        "updated_at": _now(),
    })


@router.post("/product-drafts/{draft_id}/review-edit-sessions/{session_id}/undo")
async def undo_review_edit_session(
    draft_id: str,
    session_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    session, current = _grid_session_for_action(
        draft_id, session_id, tenant, payload, ctx
    )
    history = deepcopy(session.get("history") or [])
    cursor = max(0, min(int(session.get("history_cursor") or 0), len(history)))
    if cursor == 0:
        raise _conflict("There is nothing to undo")
    pending = _products({"products": session.get("pending_products")})
    working_plan = deepcopy(session.get("working_plan") or {})
    grid_state = deepcopy(session.get("grid_state") or {})
    _replay_history_entry(
        pending, working_plan, grid_state, history[cursor - 1], direction="before"
    )
    saved = _save_history_action(
        session,
        current,
        ctx,
        cursor=cursor - 1,
        history=history,
        pending=pending,
        working_plan=working_plan,
        grid_state=grid_state,
    )
    return {"session": saved}


@router.post("/product-drafts/{draft_id}/review-edit-sessions/{session_id}/redo")
async def redo_review_edit_session(
    draft_id: str,
    session_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    session, current = _grid_session_for_action(
        draft_id, session_id, tenant, payload, ctx
    )
    history = deepcopy(session.get("history") or [])
    cursor = max(0, min(int(session.get("history_cursor") or 0), len(history)))
    if cursor >= len(history):
        raise _conflict("There is nothing to redo")
    pending = _products({"products": session.get("pending_products")})
    working_plan = deepcopy(session.get("working_plan") or {})
    grid_state = deepcopy(session.get("grid_state") or {})
    _replay_history_entry(
        pending, working_plan, grid_state, history[cursor], direction="after"
    )
    saved = _save_history_action(
        session,
        current,
        ctx,
        cursor=cursor + 1,
        history=history,
        pending=pending,
        working_plan=working_plan,
        grid_state=grid_state,
    )
    return {"session": saved}


@router.post("/product-drafts/{draft_id}/review-edit-sessions/{session_id}/reset")
async def reset_review_edit_session(
    draft_id: str,
    session_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    tenant = require_authenticated_shop_domain(request, payload.get("shop_domain"))
    session, current = _grid_session_for_action(
        draft_id, session_id, tenant, payload, ctx
    )
    history = deepcopy(session.get("history") or [])
    cursor = max(0, min(int(session.get("history_cursor") or 0), len(history)))
    pending = _products({"products": session.get("pending_products")})
    working_plan = deepcopy(session.get("working_plan") or {})
    grid_state = deepcopy(session.get("grid_state") or {})
    for entry in reversed(history[:cursor]):
        _replay_history_entry(
            pending, working_plan, grid_state, entry, direction="before"
        )
    saved = _save_history_action(
        session,
        current,
        ctx,
        cursor=0,
        history=[],
        pending=pending,
        working_plan=working_plan,
        grid_state=grid_state,
    )
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
    rebuilt_plan = plan_response["plan"]
    if session.get("surface") == "grid" and isinstance(session.get("working_plan"), dict):
        rebuilt_plan = _preserve_working_selection(rebuilt_plan, session["working_plan"])
        _service(ctx).save_mutation_plan(rebuilt_plan)
    applied = _service(ctx).save_review_edit_session({**session, "status": "applied", "updated_at": _now()})
    if isinstance(session.get("working_file_id"), str) and session["working_file_id"]:
        ctx.supabase.file.delete_file(session["working_file_id"])
    canonical_file = ctx.supabase.file.get_file(output_file_id) or {}
    return {
        "session": applied,
        "draft": saved_draft,
        "output_file_id": output_file_id,
        "output_filename": output_filename,
        "workbook_revision": canonical_file.get("content_revision") or new_revision,
        "plan": rebuilt_plan,
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
