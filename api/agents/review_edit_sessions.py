"""Recoverable, tenant-scoped editing for the consolidated import review."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import uuid
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import ValidationError

from ai.excel_writer import create_excel_bytes
from ai.excel_reader import WorkbookValidationError, read_generated_workbook
from ai.models import ProductsList
from app_context import AppContext, get_ctx
from application.use_cases.files.save_file import execute as save_file
from application.domain.shopify_mutation_plan import (
    _normalized_path_part,
    _review_row as _domain_review_row,
    _schema_from_product,
    _variant_subject,
    _variants,
)
from .mutation_plans import REVIEW_REQUIRED, UNSUPPORTED_REVIEW_CONTRACT, create_mutation_plan
from .utils import require_authenticated_shop_domain

router = APIRouter()
LOG = logging.getLogger(__name__)
REVIEW_FIELD_VALIDATION_FAILED = "REVIEW_FIELD_VALIDATION_FAILED"
CONTENT_HISTORY_OPERATIONS = {"value_edit", "value_revert", "selection"}
HANDLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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


def _field_error(row: dict[str, Any], code: str, message: str) -> dict[str, str]:
    return {
        "change_id": str(row.get("change_id") or ""),
        "value_path": str(row.get("value_path") or ""),
        "code": code,
        "message": message,
    }


def _validation_failure(
    errors: list[dict[str, str]], *, draft_id: str | None = None, session_id: str | None = None
) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": REVIEW_FIELD_VALIDATION_FAILED,
            "message": "Correct the invalid review fields before applying edits.",
            "field_errors": errors,
        },
    )


def _preflight_field_errors(
    exc: Exception, working_plan: dict[str, Any]
) -> list[dict[str, str]]:
    validation_items = exc.errors() if isinstance(exc, ValidationError) else []
    errors: list[dict[str, str]] = []
    for validation in validation_items:
        loc = validation.get("loc") if isinstance(validation, dict) else ()
        loc = tuple(loc) if isinstance(loc, (tuple, list)) else ()
        product_index = loc[1] if len(loc) > 1 and isinstance(loc[1], int) else None
        model_field = str(loc[2]) if len(loc) > 2 else "product"
        aliases = {
            "body_html": "descriptionHtml",
            "product_type": "productType",
            "compare_at_price": "compareAtPrice",
            "cost_per_item": "inventoryItem.cost",
        }
        field = aliases.get(model_field, model_field)
        matching_row: dict[str, Any] | None = None
        for item in working_plan.get("items", []):
            if not isinstance(item, dict) or item.get("source_index") != product_index:
                continue
            matching_row = next(
                (row for row in item.get("review_changes", []) if isinstance(row, dict) and row.get("field") == field),
                None,
            )
            if matching_row:
                break
        message = str(validation.get("msg") or "This value is invalid.") if isinstance(validation, dict) else "This value is invalid."
        errors.append(
            _field_error(matching_row or {"value_path": ".".join(map(str, loc))}, "INVALID_PRODUCT", message)
        )
    if not errors:
        errors.append({
            "change_id": "",
            "value_path": "product",
            "code": "INVALID_PRODUCT",
            "message": "One or more product values cannot be written to the workbook.",
        })
    return errors


def _canonical_review_value(row: dict[str, Any], value: Any) -> Any:
    schema = row.get("edit_schema") if isinstance(row.get("edit_schema"), dict) else {}
    editor = str(schema.get("editor") or "text")
    if editor == "list":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("Enter one value per line.")
        values = [item.strip() for item in value if item.strip()]
        if len({item.casefold() for item in values}) != len(values):
            raise ValueError("Values must be unique.")
        return values
    if editor == "decimal":
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if isinstance(value, bool):
            raise ValueError("Enter a non-negative number.")
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            raise ValueError("Enter a non-negative number.") from None
        if not number.is_finite() or number < 0:
            raise ValueError("Enter a finite number that is zero or greater.")
        return float(number)
    if editor == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.casefold() in {"true", "false"}:
            return value.casefold() == "true"
        raise ValueError("Choose Yes or No.")
    if editor == "enum":
        options = schema.get("options") if isinstance(schema.get("options"), list) else []
        if value not in options:
            raise ValueError("Choose one of the available values.")
        return value
    if editor == "json":
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            raise ValueError("Enter valid JSON.") from None
        return value if schema.get("preserve_string") else parsed
    if not isinstance(value, str):
        raise ValueError("Enter a text value.")
    normalized = value.strip() if schema.get("required") or schema.get("format") else value
    if schema.get("required") and not normalized:
        raise ValueError("This field is required.")
    if schema.get("format") == "handle" and normalized and not HANDLE_RE.fullmatch(normalized):
        raise ValueError("Use lowercase letters, numbers, and single hyphens only.")
    return normalized


def _option_rename_map(row: dict[str, Any], value: list[str]) -> dict[str, str]:
    before = row.get("proposed")
    if not isinstance(before, list) or any(not isinstance(item, str) for item in before):
        before = row.get("original_proposed")
    if not isinstance(before, list) or any(not isinstance(item, str) for item in before):
        raise ValueError("This option list is malformed. Revert it or edit the workbook.")
    if len(value) != len(before):
        raise ValueError("Add or remove option values in the workbook instead.")
    old_by_fold = {item.casefold(): item for item in before}
    new_by_fold = {item.casefold(): item for item in value}
    removed = [old_by_fold[key] for key in old_by_fold.keys() - new_by_fold.keys()]
    added = [new_by_fold[key] for key in new_by_fold.keys() - old_by_fold.keys()]
    if len(removed) != len(added):
        raise ValueError("Add or remove option values in the workbook instead.")
    if len(removed) > 1:
        raise ValueError("Rename one option value at a time, or edit the workbook.")
    return dict(zip(removed, added, strict=True))


def _cascade_option_renames(
    product: dict[str, Any], option: dict[str, Any], rename_map: dict[str, str]
) -> None:
    if not rename_map:
        return
    folded = {old.casefold(): new for old, new in rename_map.items()}
    options = product.get("options") if isinstance(product.get("options"), list) else []
    option_index = next((index for index, candidate in enumerate(options) if candidate is option), -1)
    option_name = str(option.get("name") or "")
    for variant in _variants(product):
        positional = f"option{option_index + 1}"
        if option_index >= 0 and isinstance(variant.get(positional), str):
            replacement = folded.get(str(variant[positional]).casefold())
            if replacement:
                variant[positional] = replacement
        for key in ("selectedOptions", "selected_options", "optionValues", "option_values"):
            pairs = variant.get(key)
            if not isinstance(pairs, list):
                continue
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                pair_name = pair.get("optionName") or pair.get("option_name") or pair.get("name")
                value_key = "value" if "value" in pair else "name"
                if str(pair_name or "").casefold() != option_name.casefold():
                    continue
                replacement = folded.get(str(pair.get(value_key) or "").casefold())
                if replacement:
                    pair[value_key] = replacement


def _cascade_plan_variant_subjects(
    working_plan: dict[str, Any], product_index: int, option_name: str, rename_map: dict[str, str]
) -> None:
    if not rename_map:
        return
    item = next(
        (
            candidate for candidate in working_plan.get("items", [])
            if isinstance(candidate, dict) and candidate.get("source_index") == product_index
        ),
        None,
    )
    if not isinstance(item, dict):
        return
    for candidate in item.get("review_changes", []):
        if not isinstance(candidate, dict) or candidate.get("scope") not in {"variant", "inventory"}:
            continue
        subject = str(candidate.get("subject") or "")
        for old, new in rename_map.items():
            subject = subject.replace(f"{option_name}: {old}", f"{option_name}: {new}")
        candidate["subject"] = subject


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
    if value_type == "json" and isinstance(value, str):
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
        if field == "values" and isinstance(value, list):
            _cascade_option_renames(product, target, _option_rename_map(row, value))
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
            if not isinstance(row.get("edit_schema"), dict):
                inferred = _domain_review_row(
                    scope=str(row.get("scope") or "product"),
                    subject=str(row.get("subject") or "Product"),
                    field=str(row.get("field") or "value"),
                    current=row.get("current"),
                    proposed=row.get("proposed"),
                    disposition=str(row.get("disposition") or "applicable"),
                    change_kind=str(row.get("change_kind") or "updated"),
                    value_type=str(row.get("value_type")) if row.get("value_type") else None,
                )
                row["edit_schema"] = inferred["edit_schema"]
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
        option_renames: dict[str, str] = {}
        if operation_type == "value_revert":
            value = deepcopy(row.get("original_proposed"))
            if row.get("scope") == "option" and row.get("field") == "values" and isinstance(value, list):
                option_renames = _option_rename_map(row, value)
            origin = str(row.get("original_origin") or "unknown")
            source_ref = row.get("original_source_ref")
        else:
            try:
                value = _canonical_review_value(row, operation.get("value"))
                if row.get("scope") == "option" and row.get("field") == "values":
                    option_renames = _option_rename_map(row, value)
            except ValueError as exc:
                raise _validation_failure([
                    _field_error(row, "INVALID_REVIEW_VALUE", str(exc))
                ]) from None
            origin = "merchant_edited"
            source_ref = None

        try:
            _set_product_value(products[product_index], row, value, origin=origin)
        except ValueError as exc:
            raise _validation_failure([
                _field_error(row, "INVALID_OPTION_VALUES", str(exc))
            ]) from None
        if option_renames:
            _cascade_plan_variant_subjects(
                working_plan, product_index, str(row.get("subject") or ""), option_renames
            )
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
) -> dict[str, Any] | None:
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
        after = deepcopy(value)
        grid_state["filterValues"] = after
        return None

    if operation_type == "expansion":
        after = _validate_string_list(
            operation.get("expandedItemIds"), "expandedItemIds"
        )
        grid_state["expandedItemIds"] = after
        return None

    if operation_type == "pagination":
        after = _validated_pages(operation.get("pagesByItem"))
        grid_state["pagesByItem"] = after
        return None

    # Older browser tabs may still flush this operation. Layout is local now,
    # so accept it as a no-op without persisting it or adding history.
    if operation_type == "workspace_view":
        if operation.get("workspaceView") not in {"split", "changes", "spreadsheet"}:
            raise HTTPException(status_code=400, detail="workspaceView is invalid")
        return None

    raise HTTPException(
        status_code=400,
        detail=(
            "operation must be value_edit, value_revert, selection, filters, "
            "expansion, pagination, or legacy workspace_view"
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
    option_renames: dict[str, str] = {}
    if row.get("scope") == "option" and row.get("field") == "values" and isinstance(proposed, list):
        option_renames = _option_rename_map(row, proposed)
    _set_product_value(products[product_index], row, proposed, origin=origin)
    if option_renames:
        _cascade_plan_variant_subjects(
            working_plan, product_index, str(row.get("subject") or ""), option_renames
        )
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
    durable_history = deepcopy(history)
    for operation in operations:
        operation_type = operation.get("operation") or operation.get("type")
        if operation_type in {"value_edit", "value_revert"}:
            products, durable_history, history_cursor = _apply_value_operations(
                products,
                working_plan,
                [operation],
                durable_history,
                history_cursor,
            )
        else:
            entry = _apply_ui_operation(working_plan, grid_state, operation)
            if entry is not None:
                durable_history = durable_history[:history_cursor]
                durable_history.append(entry)
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
    try:
        pending, history, history_cursor = _apply_operations(
            pending,
            working_plan,
            grid_state,
            operations,
            history,
            history_cursor,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if exc.status_code == 422 and detail.get("code") == REVIEW_FIELD_VALIDATION_FAILED:
            LOG.warning(
                "import_review_field_validation_failed draft_id=%s session_id=%s change_ids=%s",
                draft_id,
                session_id,
                ",".join(
                    str(error.get("change_id") or "")
                    for error in detail.get("field_errors", [])[:10]
                    if isinstance(error, dict)
                ),
            )
        raise
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
    existing_field_errors = session.get("field_errors")
    if isinstance(existing_field_errors, list) and existing_field_errors:
        LOG.warning(
            "import_review_apply_validation_failed draft_id=%s session_id=%s change_ids=%s",
            draft_id,
            session_id,
            ",".join(
                str(error.get("change_id") or "")
                for error in existing_field_errors[:10]
                if isinstance(error, dict)
            ),
        )
        raise _validation_failure([
            error for error in existing_field_errors if isinstance(error, dict)
        ])
    working_plan = session.get("working_plan") if isinstance(session.get("working_plan"), dict) else {}
    try:
        workbook_model = _workbook_model(products)
        workbook = create_excel_bytes(workbook_model)
    except (ValidationError, WorkbookValidationError, ValueError, TypeError) as exc:
        field_errors = _preflight_field_errors(exc, working_plan)
        LOG.warning(
            "import_review_apply_validation_failed draft_id=%s session_id=%s change_ids=%s",
            draft_id,
            session_id,
            ",".join(error.get("change_id", "") for error in field_errors[:10]),
        )
        raise _validation_failure(field_errors) from None
    new_revision = int(draft.get("revision") or 1) + 1
    output_file_id = str(draft.get("output_file_id") or uuid.uuid4())
    output_filename = str(draft.get("output_filename") or "generated-import.xlsx")
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
