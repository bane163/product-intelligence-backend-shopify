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
    OptionValueEditError,
    _normalized_path_part,
    _review_row as _domain_review_row,
    _schema_from_product,
    _unique_variant_target,
    _variant_subject,
    _variants,
    apply_option_value_operation,
    build_review_plan_item,
    normalize_review_plan_rows,
)
from .mutation_plans import (
    REVIEW_REQUIRED,
    UNSUPPORTED_REVIEW_CONTRACT,
    create_mutation_plan,
)
from .utils import require_authenticated_shop_domain

router = APIRouter()
LOG = logging.getLogger(__name__)
REVIEW_FIELD_VALIDATION_FAILED = "REVIEW_FIELD_VALIDATION_FAILED"
CONTENT_HISTORY_OPERATIONS = {
    "value_edit",
    "value_revert",
    "option_values_edit",
    "selection",
}
HANDLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _service(ctx: AppContext) -> Any:
    return getattr(ctx.supabase, "_service", ctx.supabase)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=409, detail={"code": REVIEW_REQUIRED, "message": message}
    )


def _products(draft: dict[str, Any]) -> list[dict[str, Any]]:
    value = draft.get("products")
    return (
        deepcopy([item for item in value if isinstance(item, dict)])
        if isinstance(value, list)
        else []
    )


def _require_review_v2(
    ctx: AppContext, draft_id: str, tenant: str
) -> dict[str, Any] | None:
    plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if isinstance(plan, dict) and plan.get("review_contract_version") not in {2, 3, 4}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": UNSUPPORTED_REVIEW_CONTRACT,
                "message": "Regenerate this review plan before editing",
            },
        )
    return plan


def _workbook_model(products: list[dict[str, Any]]) -> ProductsList:
    normalized = deepcopy(products)
    for product in normalized:
        if isinstance(product.get("_value_origins"), dict) and not isinstance(
            product.get("value_origins"), dict
        ):
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
    errors: list[dict[str, str]],
    *,
    draft_id: str | None = None,
    session_id: str | None = None,
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
                (
                    row
                    for row in item.get("review_changes", [])
                    if isinstance(row, dict) and row.get("field") == field
                ),
                None,
            )
            if matching_row:
                break
        message = (
            str(validation.get("msg") or "This value is invalid.")
            if isinstance(validation, dict)
            else "This value is invalid."
        )
        errors.append(
            _field_error(
                matching_row or {"value_path": ".".join(map(str, loc))},
                "INVALID_PRODUCT",
                message,
            )
        )
    if not errors:
        errors.append(
            {
                "change_id": "",
                "value_path": "product",
                "code": "INVALID_PRODUCT",
                "message": "One or more product values cannot be written to the workbook.",
            }
        )
    return errors


def _canonical_review_value(row: dict[str, Any], value: Any) -> Any:
    schema = row.get("edit_schema") if isinstance(row.get("edit_schema"), dict) else {}
    editor = str(schema.get("editor") or "text")
    if editor == "list":
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError("Enter one value per line.")
        values = [item.strip() for item in value if item.strip()]
        if len({item.casefold() for item in values}) != len(values):
            raise ValueError("Values must be unique.")
        return values
    if editor == "decimal":
        if value is None or (isinstance(value, str) and not value.strip()):
            if schema.get("required"):
                raise ValueError("This field is required.")
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
    if editor == "integer":
        from application.domain.shopify_mutation_plan import validate_inventory_quantity

        return validate_inventory_quantity(value)
    if editor == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.casefold() in {"true", "false"}:
            return value.casefold() == "true"
        raise ValueError("Choose Yes or No.")
    if editor == "enum":
        options = (
            schema.get("options") if isinstance(schema.get("options"), list) else []
        )
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
    normalized = (
        value.strip() if schema.get("required") or schema.get("format") else value
    )
    if schema.get("required") and not normalized:
        raise ValueError("This field is required.")
    if (
        schema.get("format") == "handle"
        and normalized
        and not HANDLE_RE.fullmatch(normalized)
    ):
        raise ValueError("Use lowercase letters, numbers, and single hyphens only.")
    return normalized


def _option_rename_map(row: dict[str, Any], value: list[str]) -> dict[str, str]:
    before = row.get("proposed")
    if not isinstance(before, list) or any(
        not isinstance(item, str) for item in before
    ):
        before = row.get("original_proposed")
    if not isinstance(before, list) or any(
        not isinstance(item, str) for item in before
    ):
        raise ValueError(
            "This option list is malformed. Revert it or edit the workbook."
        )
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
    option_index = next(
        (index for index, candidate in enumerate(options) if candidate is option), -1
    )
    option_name = str(option.get("name") or "")
    for variant in _variants(product):
        positional = f"option{option_index + 1}"
        if option_index >= 0 and isinstance(variant.get(positional), str):
            replacement = folded.get(str(variant[positional]).casefold())
            if replacement:
                variant[positional] = replacement
        for key in (
            "selectedOptions",
            "selected_options",
            "optionValues",
            "option_values",
        ):
            pairs = variant.get(key)
            if not isinstance(pairs, list):
                continue
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                pair_name = (
                    pair.get("optionName")
                    or pair.get("option_name")
                    or pair.get("name")
                )
                value_key = "value" if "value" in pair else "name"
                if str(pair_name or "").casefold() != option_name.casefold():
                    continue
                replacement = folded.get(str(pair.get(value_key) or "").casefold())
                if replacement:
                    pair[value_key] = replacement


def _cascade_plan_variant_subjects(
    working_plan: dict[str, Any],
    product_index: int,
    option_name: str,
    rename_map: dict[str, str],
) -> None:
    if not rename_map:
        return
    item = next(
        (
            candidate
            for candidate in working_plan.get("items", [])
            if isinstance(candidate, dict)
            and candidate.get("source_index") == product_index
        ),
        None,
    )
    if not isinstance(item, dict):
        return
    for candidate in item.get("review_changes", []):
        if not isinstance(candidate, dict) or candidate.get("scope") not in {
            "variant",
            "inventory",
        }:
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
        original_options = (
            original.get("options") if isinstance(original.get("options"), list) else []
        )
        for option_index, option in enumerate(
            product.get("options") if isinstance(product.get("options"), list) else []
        ):
            old_option = (
                original_options[option_index]
                if option_index < len(original_options)
                and isinstance(original_options[option_index], dict)
                else {}
            )
            if not isinstance(option, dict):
                continue
            subject = (
                option.get("name")
                or old_option.get("name")
                or f"Option {option_index + 1}"
            )
            for field in option_fields:
                if option.get(field) != old_option.get(field):
                    origins[f"option[{_normalized_path_part(subject)}].{field}"] = (
                        "merchant_edited"
                    )
        option_names = _schema_from_product(product)
        old_variants = _variants(original)
        variant_aliases = {
            "compare_at_price": "compareAtPrice",
            "inventory_policy": "inventoryPolicy",
            "tax_code": "taxCode",
            "cost_per_item": "inventoryItem.cost",
            "tracked": "inventoryItem.tracked",
            "requires_shipping": "inventoryItem.requiresShipping",
            "weight": "inventoryItem.weight.value",
            "weight_unit": "inventoryItem.weight.unit",
            "inventory_quantity": "inventory_quantity",
        }
        for variant_index, variant in enumerate(_variants(product)):
            old_variant = (
                old_variants[variant_index] if variant_index < len(old_variants) else {}
            )
            subject = _variant_subject(variant, option_names)
            for field in (
                "price",
                "compare_at_price",
                "sku",
                "barcode",
                "taxable",
                "tax_code",
                "inventory_policy",
                "cost_per_item",
                "tracked",
                "requires_shipping",
                "weight",
                "weight_unit",
                "inventory_quantity",
            ):
                if variant.get(field) != old_variant.get(field):
                    canonical = variant_aliases.get(field, field)
                    scope = (
                        "inventory"
                        if canonical == "inventory_quantity"
                        or (
                            canonical == "inventoryItem.tracked"
                            and "inventory_quantity" in variant
                        )
                        else "variant"
                    )
                    origins[
                        f"{scope}[{_normalized_path_part(subject)}].{canonical}"
                    ] = "merchant_edited"
        old_metafields = (
            original.get("metafields")
            if isinstance(original.get("metafields"), list)
            else []
        )
        for mf_index, metafield in enumerate(
            product.get("metafields")
            if isinstance(product.get("metafields"), list)
            else []
        ):
            old_metafield = (
                old_metafields[mf_index]
                if mf_index < len(old_metafields)
                and isinstance(old_metafields[mf_index], dict)
                else {}
            )
            if not isinstance(metafield, dict):
                continue
            subject = f"{metafield.get('namespace')}.{metafield.get('key')}"
            for field in ("value", "type"):
                if metafield.get(field) != old_metafield.get(field):
                    origins[f"metafield[{_normalized_path_part(subject)}].{field}"] = (
                        "merchant_edited"
                    )
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
    if value_type == "integer":
        try:
            from application.domain.shopify_mutation_plan import (
                validate_inventory_quantity,
            )

            return validate_inventory_quantity(value)
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
            raise HTTPException(
                status_code=422, detail=f"Grid editing is not supported for {path}"
            )
        product[resolved] = value
    elif scope in {"variant", "inventory"}:
        target = _unique_variant_target(product, row)
        if target is None:
            raise HTTPException(
                status_code=422,
                detail=f"Variant target is unavailable or ambiguous for {path}",
            )
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
        options = (
            product.get("options") if isinstance(product.get("options"), list) else []
        )
        target = next(
            (
                option
                for option in options
                if isinstance(option, dict) and str(option.get("name") or "") == subject
            ),
            None,
        )
        if target is None:
            raise HTTPException(
                status_code=422, detail=f"Option is unavailable for {path}"
            )
        if field == "values" and isinstance(value, list):
            _cascade_option_renames(product, target, _option_rename_map(row, value))
        target[field] = value
    elif scope == "metafield":
        namespace, _, key = subject.partition(".")
        metafields = (
            product.get("metafields")
            if isinstance(product.get("metafields"), list)
            else []
        )
        target = next(
            (
                item
                for item in metafields
                if isinstance(item, dict)
                and item.get("namespace") == namespace
                and item.get("key") == key
            ),
            None,
        )
        if target is None:
            raise HTTPException(
                status_code=422, detail=f"Metafield is unavailable for {path}"
            )
        target[field] = value
    else:
        raise HTTPException(
            status_code=422, detail=f"Grid editing is not supported for {path}"
        )
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


def _set_item_proposed_value(
    item: dict[str, Any], row: dict[str, Any], value: Any
) -> None:
    proposed = item.get("proposed")
    if not isinstance(proposed, dict):
        return
    scope = str(row.get("scope") or "product")
    field = str(row.get("field") or "")
    subject = str(row.get("subject") or "")

    def set_path(target: dict[str, Any], path: str, next_value: Any) -> None:
        parts = path.split(".")
        container = target
        for part in parts[:-1]:
            child = container.get(part)
            if not isinstance(child, dict):
                child = {}
                container[part] = child
            container = child
        container[parts[-1]] = deepcopy(next_value)

    if scope == "product":
        set_path(proposed, str(row.get("value_path") or field), value)
        return
    if scope in {"variant", "inventory"}:
        target = _unique_variant_target(proposed, row)
        if isinstance(target, dict):
            set_path(target, field, value)
        return
    if scope == "metafield":
        namespace, _, key = subject.partition(".")
        metafields = (
            proposed.get("metafields")
            if isinstance(proposed.get("metafields"), list)
            else []
        )
        target = next(
            (
                metafield
                for metafield in metafields
                if isinstance(metafield, dict)
                and metafield.get("namespace") == namespace
                and metafield.get("key") == key
            ),
            None,
        )
        if isinstance(target, dict):
            set_path(target, field, value)


def _snapshot_working_plan(
    plan: dict[str, Any], products: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Freeze the proposal and provenance that per-value revert must restore."""
    working_plan = deepcopy(plan)
    for item in working_plan.get("items", []):
        if not isinstance(item, dict):
            continue
        source_index = item.get("source_index")
        if (
            not isinstance(item.get("proposed"), dict)
            and isinstance(source_index, int)
            and isinstance(products, list)
            and 0 <= source_index < len(products)
        ):
            proposed = deepcopy(products[source_index])
            raw_options = proposed.get("options")
            if isinstance(raw_options, list):
                proposed["productOptions"] = [
                    {
                        **deepcopy(option),
                        "values": [
                            (
                                deepcopy(value)
                                if isinstance(value, dict)
                                else {"name": value}
                            )
                            for value in option.get("values", [])
                        ],
                    }
                    for option in raw_options
                    if isinstance(option, dict)
                ]
            item["proposed"] = proposed
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
                    value_type=(
                        str(row.get("value_type")) if row.get("value_type") else None
                    ),
                )
                row["edit_schema"] = inferred["edit_schema"]
            row.setdefault("original_proposed", deepcopy(row.get("proposed")))
            row.setdefault("original_origin", row.get("origin", "unknown"))
            row.setdefault(
                "original_source_ref",
                (
                    deepcopy(row.get("source_ref"))
                    if isinstance(row.get("source_ref"), dict)
                    else None
                ),
            )
            row.setdefault(
                "original_source_refs",
                (
                    deepcopy(row.get("source_refs"))
                    if isinstance(row.get("source_refs"), list)
                    else []
                ),
            )
    return normalize_review_plan_rows(working_plan)


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


def _working_row_match(
    plan: dict[str, Any], change_id: str
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    matches: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for item in plan.get("items", []):
        if not isinstance(item, dict) or not isinstance(item.get("source_index"), int):
            continue
        for row in item.get("review_changes", []):
            if isinstance(row, dict) and str(row.get("change_id") or "") == change_id:
                matches.append((int(item["source_index"]), item, row))
    if not matches:
        raise _validation_failure(
            [
                {
                    "change_id": change_id,
                    "value_path": "",
                    "code": "REVIEW_TARGET_MISSING",
                    "message": "This review field is no longer available. Refresh and try again.",
                }
            ]
        )
    if len(matches) != 1:
        raise _validation_failure(
            [
                {
                    "change_id": change_id,
                    "value_path": "",
                    "code": "AMBIGUOUS_REVIEW_TARGET",
                    "message": "This review field is ambiguous. Refresh before editing it.",
                }
            ]
        )
    return matches[0]


def _require_editable_row(row: dict[str, Any], *, option_values: bool = False) -> None:
    if row.get("editable") is not True:
        raise _validation_failure(
            [
                _field_error(
                    row,
                    "REVIEW_FIELD_READ_ONLY",
                    "This review field is read-only or no longer available.",
                )
            ]
        )
    if option_values and not (
        row.get("scope") == "option" and row.get("field") == "values"
    ):
        raise _validation_failure(
            [
                _field_error(
                    row,
                    "INVALID_OPTION_TARGET",
                    "This operation can only edit option values.",
                )
            ]
        )


def _replace_working_item(
    working_plan: dict[str, Any], item_id: str, replacement: dict[str, Any]
) -> None:
    for index, item in enumerate(working_plan.get("items", [])):
        if isinstance(item, dict) and str(item.get("item_id") or "") == item_id:
            working_plan["items"][index] = deepcopy(replacement)
            return
    raise HTTPException(status_code=409, detail="History item is unavailable")


def _structural_state(product: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {"product": deepcopy(product), "item": deepcopy(item)}


def _restore_structural_state(
    products: list[dict[str, Any]],
    working_plan: dict[str, Any],
    entry: dict[str, Any],
    state: dict[str, Any],
) -> None:
    product_index = entry.get("product_index")
    product = state.get("product")
    item = state.get("item")
    if (
        not isinstance(product_index, int)
        or isinstance(product_index, bool)
        or product_index < 0
        or product_index >= len(products)
        or not isinstance(product, dict)
        or not isinstance(item, dict)
    ):
        raise HTTPException(status_code=409, detail="Option history is invalid")
    products[product_index] = deepcopy(product)
    _replace_working_item(working_plan, str(entry.get("item_id") or ""), deepcopy(item))


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
                and (
                    row.get("required")
                    or str(row.get("change_id") or "") in included_ids
                )
            )
    return result


def _validate_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HTTPException(
            status_code=400, detail=f"{field} must be an array of strings"
        )
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
        raise HTTPException(
            status_code=400, detail="Use operation or operations, not both"
        )
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
            raise HTTPException(
                status_code=400, detail="operations must be an array of objects"
            )
        return raw
    return None


def _apply_value_operations(
    products: list[dict[str, Any]],
    working_plan: dict[str, Any],
    operations: list[dict[str, Any]],
    history: list[dict[str, Any]],
    history_cursor: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    appended: list[dict[str, Any]] = []
    for operation in operations:
        operation_type = operation.get("operation") or operation.get("type")
        if operation_type not in {"value_edit", "value_revert"}:
            raise HTTPException(
                status_code=400, detail="operation must be value_edit or value_revert"
            )
        change_id = str(operation.get("change_id") or "")
        product_index, _item, row = _working_row_match(working_plan, change_id)
        path = str(operation.get("value_path") or "")
        if path != row.get("value_path"):
            raise _validation_failure(
                [
                    _field_error(
                        row,
                        "STALE_VALUE_PATH",
                        "This review field changed. Refresh before editing it.",
                    )
                ]
            )
        if product_index >= len(products):
            raise _validation_failure(
                [
                    _field_error(
                        row,
                        "REVIEW_TARGET_MISSING",
                        "This product is no longer available.",
                    )
                ]
            )
        _require_editable_row(row)
        if row.get("scope") == "option" and row.get("field") == "values":
            raise _validation_failure(
                [
                    _field_error(
                        row,
                        "OPTION_VALUES_OPERATION_REQUIRED",
                        "Use an option-value action to edit this field.",
                    )
                ]
            )

        before = {
            "proposed": deepcopy(row.get("proposed")),
            "origin": row.get("origin", "unknown"),
            "source_ref": (
                deepcopy(row.get("source_ref"))
                if isinstance(row.get("source_ref"), dict)
                else None
            ),
            "source_refs": (
                deepcopy(row.get("source_refs"))
                if isinstance(row.get("source_refs"), list)
                else []
            ),
        }
        option_renames: dict[str, str] = {}
        if operation_type == "value_revert":
            value = deepcopy(row.get("original_proposed"))
            if (
                row.get("scope") == "option"
                and row.get("field") == "values"
                and isinstance(value, list)
            ):
                option_renames = _option_rename_map(row, value)
            origin = str(row.get("original_origin") or "unknown")
            source_ref = row.get("original_source_ref")
            source_refs = row.get("original_source_refs")
        else:
            try:
                value = _canonical_review_value(row, operation.get("value"))
                if row.get("scope") == "option" and row.get("field") == "values":
                    option_renames = _option_rename_map(row, value)
            except ValueError as exc:
                raise _validation_failure(
                    [_field_error(row, "INVALID_REVIEW_VALUE", str(exc))]
                ) from None
            origin = "merchant_edited"
            source_ref = None
            source_refs = [
                deepcopy(ref)
                for ref in row.get("source_refs", [])
                if isinstance(ref, dict)
                and ref.get("evidence_role", "value") in {"label", "key"}
            ]

        try:
            _set_product_value(products[product_index], row, value, origin=origin)
        except ValueError as exc:
            raise _validation_failure(
                [_field_error(row, "INVALID_OPTION_VALUES", str(exc))]
            ) from None
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            raise _validation_failure(
                [
                    _field_error(
                        row,
                        "REVIEW_TARGET_MISSING",
                        "This review field is no longer available. Refresh and try again.",
                    )
                ]
            ) from None
        item = next(
            (
                candidate
                for candidate in working_plan.get("items", [])
                if isinstance(candidate, dict)
                and candidate.get("source_index") == product_index
            ),
            None,
        )
        if isinstance(item, dict):
            _set_item_proposed_value(item, row, value)
        if option_renames:
            _cascade_plan_variant_subjects(
                working_plan,
                product_index,
                str(row.get("subject") or ""),
                option_renames,
            )
        row["proposed"] = deepcopy(_coerce_review_value(row, value))
        row["origin"] = origin
        if isinstance(source_ref, dict):
            row["source_ref"] = deepcopy(source_ref)
        else:
            row.pop("source_ref", None)
        if isinstance(source_refs, list) and source_refs:
            row["source_refs"] = deepcopy(source_refs)
        else:
            row.pop("source_refs", None)
        after = {
            "proposed": deepcopy(row.get("proposed")),
            "origin": row["origin"],
            "source_ref": (
                deepcopy(row.get("source_ref"))
                if isinstance(row.get("source_ref"), dict)
                else None
            ),
            "source_refs": (
                deepcopy(row.get("source_refs"))
                if isinstance(row.get("source_refs"), list)
                else []
            ),
        }
        appended.append(
            _history_entry(
                str(operation_type),
                before,
                after,
                change_id=change_id,
                value_path=path,
            )
        )

    # A fresh operation after undo replaces the abandoned redo branch.
    durable_history = deepcopy(history[:history_cursor])
    durable_history.extend(appended)
    return products, durable_history, len(durable_history)


def _transform_option_state(
    product: dict[str, Any],
    item: dict[str, Any],
    row: dict[str, Any],
    operation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    action = str(operation.get("action") or "")
    edited_product = apply_option_value_operation(
        product,
        option_name=str(row.get("subject") or ""),
        action=action,
        from_value=operation.get("from"),
        to_value=operation.get("to"),
        value=operation.get("value"),
        values=operation.get("values"),
    )
    edited_proposed = apply_option_value_operation(
        item.get("proposed") if isinstance(item.get("proposed"), dict) else {},
        option_name=str(row.get("subject") or ""),
        action=action,
        from_value=operation.get("from"),
        to_value=operation.get("to"),
        value=operation.get("value"),
        values=operation.get("values"),
    )
    origin_maps = [
        candidate
        for candidate in (
            edited_product.get("value_origins"),
            edited_product.get("_value_origins"),
        )
        if isinstance(candidate, dict)
    ]
    if not origin_maps:
        edited_product["value_origins"] = {}
        origin_maps = [edited_product["value_origins"]]
    option_path = f"option[{_normalized_path_part(row.get('subject'))}].values"
    for origins in origin_maps:
        origins[option_path] = "merchant_edited"
    variant_identity_rename: tuple[int, str, str] | None = None
    if action == "rename":
        old_value = str(operation.get("from") or "")
        new_value = str(operation.get("to") or "")
        option_name = str(row.get("subject") or "")
        option_names = _schema_from_product(
            item.get("proposed") if isinstance(item.get("proposed"), dict) else {}
        )
        option_index = next(
            (
                index
                for index, name in enumerate(option_names)
                if name.casefold() == option_name.casefold()
            ),
            None,
        )
        if option_index is not None:
            variant_identity_rename = (
                option_index,
                new_value.casefold(),
                old_value.casefold(),
            )
    rebuilt = build_review_plan_item(
        item_id=str(item.get("item_id") or ""),
        currency_code=str(item.get("currency_code") or "USD"),
        source_index=int(item.get("source_index") or 0),
        source_product=edited_product,
        live_snapshot=(
            item.get("live_snapshot")
            if isinstance(item.get("live_snapshot"), dict)
            else None
        ),
        proposed=edited_proposed,
        operation=str(item.get("operation") or "update"),
        blockers=item.get("blockers") if isinstance(item.get("blockers"), list) else [],
        unapplied=(
            item.get("unapplied") if isinstance(item.get("unapplied"), list) else []
        ),
        prior_item=item,
        variant_identity_rename=variant_identity_rename,
    )
    return edited_product, rebuilt


def _apply_option_value_operation(
    products: list[dict[str, Any]],
    working_plan: dict[str, Any],
    operation: dict[str, Any],
    history: list[dict[str, Any]],
    history_cursor: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    change_id = str(operation.get("change_id") or "")
    product_index, item, row = _working_row_match(working_plan, change_id)
    path = str(operation.get("value_path") or "")
    if path != str(row.get("value_path") or ""):
        raise _validation_failure(
            [
                _field_error(
                    row,
                    "STALE_VALUE_PATH",
                    "This review field changed. Refresh before editing it.",
                )
            ]
        )
    _require_editable_row(row, option_values=True)
    if product_index >= len(products):
        raise _validation_failure(
            [
                _field_error(
                    row,
                    "REVIEW_TARGET_MISSING",
                    "This product is no longer available.",
                )
            ]
        )
    action = str(operation.get("action") or "")
    before = _structural_state(products[product_index], item)
    option_bases = working_plan.setdefault("_option_edit_bases", {})
    if isinstance(option_bases, dict) and change_id not in option_bases:
        option_bases[change_id] = deepcopy(before)
    option_initials = working_plan.setdefault("_option_edit_initials", {})
    item_id = str(item.get("item_id") or "")
    if isinstance(option_initials, dict) and item_id not in option_initials:
        option_initials[item_id] = deepcopy(before)
    try:
        edited_product, rebuilt = _transform_option_state(
            products[product_index], item, row, operation
        )
    except OptionValueEditError as exc:
        raise _validation_failure([_field_error(row, exc.code, str(exc))]) from None
    products[product_index] = edited_product
    _replace_working_item(working_plan, str(item.get("item_id") or ""), rebuilt)
    after = _structural_state(edited_product, rebuilt)
    entry = _history_entry(
        "option_values_edit",
        before,
        after,
        change_id=change_id,
        value_path=path,
        item_id=str(item.get("item_id") or ""),
        product_index=product_index,
        action=action,
        option_subject=str(row.get("subject") or ""),
        payload=deepcopy(operation),
    )
    durable_history = deepcopy(history[:history_cursor])
    durable_history.append(entry)
    return products, durable_history, len(durable_history)


def _apply_option_value_revert(
    products: list[dict[str, Any]],
    working_plan: dict[str, Any],
    operation: dict[str, Any],
    history: list[dict[str, Any]],
    history_cursor: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    change_id = str(operation.get("change_id") or "")
    product_index, item, row = _working_row_match(working_plan, change_id)
    path = str(operation.get("value_path") or "")
    if path != str(row.get("value_path") or ""):
        raise _validation_failure(
            [
                _field_error(
                    row, "STALE_VALUE_PATH", "Refresh before reverting this field."
                )
            ]
        )
    _require_editable_row(row, option_values=True)
    bases = working_plan.get("_option_edit_bases")
    base = bases.get(change_id) if isinstance(bases, dict) else None
    if not isinstance(base, dict):
        return products, deepcopy(history), history_cursor
    before = _structural_state(products[product_index], item)
    current_selection = _selection_state(item)
    item_id = str(item.get("item_id") or "")
    initials = working_plan.get("_option_edit_initials")
    initial = initials.get(item_id) if isinstance(initials, dict) else None
    if not isinstance(initial, dict):
        initial = base
    synthetic = {
        "product_index": product_index,
        "item_id": item_id,
    }
    _restore_structural_state(products, working_plan, synthetic, initial)
    target_subject = str(row.get("subject") or "")
    active_option_edits: list[dict[str, Any]] = []
    for entry in history[:history_cursor]:
        if not isinstance(entry, dict) or str(entry.get("item_id") or "") != item_id:
            continue
        entry_subject = str(entry.get("option_subject") or "")
        if entry.get("operation") == "option_values_edit":
            active_option_edits.append(entry)
        elif (
            entry.get("operation") == "value_revert"
            and entry.get("structural")
            and entry_subject
        ):
            active_option_edits = [
                candidate
                for candidate in active_option_edits
                if str(candidate.get("option_subject") or "").casefold()
                != entry_subject.casefold()
            ]
    active_option_edits = [
        entry
        for entry in active_option_edits
        if str(entry.get("option_subject") or "").casefold()
        != target_subject.casefold()
    ]
    for option_entry in active_option_edits:
        payload = option_entry.get("payload")
        restored_item = _working_items(working_plan).get(item_id)
        if not isinstance(payload, dict) or not isinstance(restored_item, dict):
            continue
        other_subject = str(option_entry.get("option_subject") or "")
        other_row = next(
            (
                candidate
                for candidate in restored_item.get("review_changes", [])
                if isinstance(candidate, dict)
                and candidate.get("scope") == "option"
                and candidate.get("field") == "values"
                and str(candidate.get("subject") or "").casefold()
                == other_subject.casefold()
            ),
            None,
        )
        if not isinstance(other_row, dict):
            continue
        try:
            replayed_product, replayed_item = _transform_option_state(
                products[product_index],
                restored_item,
                other_row,
                payload,
            )
        except OptionValueEditError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Option history cannot be replayed: {exc}",
            ) from None
        products[product_index] = replayed_product
        _replace_working_item(working_plan, item_id, replayed_item)
    for later in history[:history_cursor]:
        if not isinstance(later, dict) or later.get("operation") not in {
            "value_edit",
            "value_revert",
        }:
            continue
        if later.get("structural"):
            continue
        try:
            _replay_history_entry(
                products,
                working_plan,
                {},
                later,
                direction="after",
            )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            # A scalar row belonging only to a reverted option no longer exists.
            continue
    restored_item = _working_items(working_plan)[item_id]
    _set_selection_state(restored_item, current_selection)
    after = _structural_state(products[product_index], restored_item)
    entry = _history_entry(
        "value_revert",
        before,
        after,
        change_id=change_id,
        value_path=path,
        item_id=item_id,
        product_index=product_index,
        structural=True,
        option_subject=target_subject,
    )
    durable_history = deepcopy(history[:history_cursor])
    durable_history.append(entry)
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
            raise HTTPException(
                status_code=422, detail=f"Item is not selectable: {item_id}"
            )
        rows = [row for row in item.get("review_changes", []) if isinstance(row, dict)]
        requested_ids = set(included_ids)
        required_ids = {
            str(row.get("change_id"))
            for row in rows
            if row.get("required") and row.get("disposition") in {None, "applicable"}
        }
        disallowed_ids = {
            str(row.get("change_id"))
            for row in rows
            if row.get("disposition") in {"blocked", "deferred"}
        }
        if selected and not required_ids.issubset(requested_ids):
            raise HTTPException(
                status_code=422,
                detail="Required or structurally dependent changes cannot be excluded",
            )
        if requested_ids & disallowed_ids:
            raise HTTPException(
                status_code=422,
                detail="Blocked or deferred changes cannot be included",
            )
        before = _selection_state(item)
        after = {"selected": selected, "included_change_ids": included_ids}
        _set_selection_state(item, after)
        return _history_entry("selection", before, after, item_id=item_id)

    if operation_type == "filters":
        value = operation.get("filterValues")
        if not isinstance(value, dict):
            raise HTTPException(
                status_code=400, detail="filterValues must be an object"
            )
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

    if operation_type == "workspace_view":
        after = operation.get("workspaceView")
        if after not in {"split", "changes", "spreadsheet"}:
            raise HTTPException(status_code=400, detail="workspaceView is invalid")
        # Legacy tabs may still send this ephemeral UI preference. Accept it,
        # but intentionally do not persist it or add it to review history.
        return None

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
        raise HTTPException(
            status_code=409, detail=f"History row is unavailable: {change_id}"
        )
    product_index, row = match
    if product_index >= len(products):
        raise HTTPException(status_code=409, detail="History product is unavailable")
    proposed = deepcopy(state.get("proposed"))
    origin = str(state.get("origin") or "unknown")
    option_renames: dict[str, str] = {}
    if (
        row.get("scope") == "option"
        and row.get("field") == "values"
        and isinstance(proposed, list)
    ):
        option_renames = _option_rename_map(row, proposed)
    _set_product_value(products[product_index], row, proposed, origin=origin)
    item = next(
        (
            candidate
            for candidate in working_plan.get("items", [])
            if isinstance(candidate, dict)
            and candidate.get("source_index") == product_index
        ),
        None,
    )
    if isinstance(item, dict):
        _set_item_proposed_value(item, row, proposed)
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
    source_refs = state.get("source_refs")
    if isinstance(source_refs, list) and source_refs:
        row["source_refs"] = deepcopy(source_refs)
    else:
        row.pop("source_refs", None)


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
    if operation == "option_values_edit" or (
        operation == "value_revert" and entry.get("structural")
    ):
        if not isinstance(state, dict):
            raise HTTPException(status_code=409, detail="Option history is invalid")
        _restore_structural_state(products, working_plan, entry, state)
    elif operation in {"value_edit", "value_revert"}:
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
        if operation_type == "option_values_edit":
            products, durable_history, history_cursor = _apply_option_value_operation(
                products,
                working_plan,
                operation,
                durable_history,
                history_cursor,
            )
        elif operation_type == "value_revert":
            change_id = str(operation.get("change_id") or "")
            _product_index, _item, row = _working_row_match(working_plan, change_id)
            if row.get("scope") == "option" and row.get("field") == "values":
                products, durable_history, history_cursor = _apply_option_value_revert(
                    products,
                    working_plan,
                    operation,
                    durable_history,
                    history_cursor,
                )
            else:
                products, durable_history, history_cursor = _apply_value_operations(
                    products,
                    working_plan,
                    [operation],
                    durable_history,
                    history_cursor,
                )
        elif operation_type == "value_edit":
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
            raise HTTPException(
                status_code=422, detail=f"Unknown change_id: {change_id}"
            )
        product_index, row = match
        path = str(change.get("value_path") or "")
        if path != row.get("value_path"):
            raise HTTPException(
                status_code=422, detail=f"value_path does not match {change_id}"
            )
        if product_index >= len(products):
            raise HTTPException(
                status_code=422, detail="Change targets a missing product"
            )
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
        if (
            active.get("surface") == surface
            and int(active.get("base_draft_revision") or 0) == current_revision
        ):
            return {"session": active, "resumed": True}
        raise _conflict(
            "Apply or discard the active edit session before switching surfaces"
        )

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
    session = _service(ctx).save_review_edit_session(
        {
            "session_id": str(uuid.uuid4()),
            "draft_id": draft_id,
            "shop_domain": tenant,
            "surface": surface,
            "base_draft_revision": current_revision,
            "edit_revision": 1,
            "pending_products": products,
            "working_plan": (
                _snapshot_working_plan(plan, products) if isinstance(plan, dict) else {}
            ),
            "base_plan_id": plan.get("plan_id") if isinstance(plan, dict) else None,
            "grid_state": (
                deepcopy(payload.get("grid_state"))
                if isinstance(payload.get("grid_state"), dict)
                else _service(ctx)._default_review_grid_state()
            ),
            "history": [],
            "history_cursor": 0,
            "working_file_id": working_file_id,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(hours=24)
            ).isoformat(),
        }
    )
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
    if session.get("status") != "active" or session.get("surface") not in {
        "grid",
        "workbook",
    }:
        raise _conflict("Review edit session is not active")
    expected = payload.get("expected_edit_revision")
    current = int(session.get("edit_revision") or 1)
    if not isinstance(expected, int) or expected != current:
        raw_operations: list[Any] = []
        if isinstance(payload.get("operation"), dict):
            raw_operations = [payload["operation"]]
        elif isinstance(payload.get("operations"), list):
            raw_operations = payload["operations"]
        structural = next(
            (
                operation
                for operation in raw_operations
                if isinstance(operation, dict)
                and (operation.get("type") or operation.get("operation"))
                == "option_values_edit"
            ),
            None,
        )
        if isinstance(structural, dict):
            change_id = str(structural.get("change_id") or "")
            plan = (
                session.get("working_plan")
                if isinstance(session.get("working_plan"), dict)
                else {}
            )
            try:
                _product_index, _item, row = _working_row_match(plan, change_id)
            except HTTPException:
                row = {
                    "change_id": change_id,
                    "value_path": str(structural.get("value_path") or ""),
                }
            raise _validation_failure(
                [
                    _field_error(
                        row,
                        "STALE_EDIT_REVISION",
                        "This review changed. Refresh before editing option values.",
                    )
                ]
            )
        raise _conflict("Edit revision is stale")
    latest_plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if not isinstance(latest_plan, dict):
        raise _conflict("Mutation plan is missing")
    if latest_plan.get("review_contract_version") not in {2, 3, 4}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": UNSUPPORTED_REVIEW_CONTRACT,
                "message": "Regenerate this review plan before editing",
            },
        )
    pending = _products({"products": session.get("pending_products")})
    working_plan = session.get("working_plan")
    if not isinstance(working_plan, dict) or not working_plan.get("items"):
        working_plan = _snapshot_working_plan(latest_plan, pending)
    else:
        working_plan = _snapshot_working_plan(working_plan, pending)
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
            (
                {**change, "operation": "value_edit"}
                if isinstance(change, dict)
                else change
            )
            for change in changes
        ]
    if any(not isinstance(operation, dict) for operation in operations):
        raise HTTPException(status_code=400, detail="changes must contain objects")
    if session.get("surface") == "workbook":
        ui_operations = {"filters", "expansion", "pagination", "workspace_view"}
        invalid = next(
            (
                operation
                for operation in operations
                if (operation.get("type") or operation.get("operation"))
                not in ui_operations
            ),
            None,
        )
        if invalid is not None:
            raise _conflict("Workbook edit sessions accept UI-state operations only")
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
        if (
            exc.status_code == 422
            and detail.get("code") == REVIEW_FIELD_VALIDATION_FAILED
        ):
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
    saved = _service(ctx).save_review_edit_session(
        {
            **session,
            "pending_products": pending,
            "working_plan": working_plan,
            "grid_state": grid_state,
            "history": history,
            "history_cursor": history_cursor,
            "edit_revision": current + 1,
            "updated_at": _now(),
        }
    )
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
    if (
        not isinstance(expected, int)
        or isinstance(expected, bool)
        or expected != current
    ):
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
    return _service(ctx).save_review_edit_session(
        {
            **session,
            "pending_products": pending,
            "working_plan": working_plan,
            "grid_state": grid_state,
            "history": history,
            "history_cursor": cursor,
            "edit_revision": current + 1,
            "updated_at": _now(),
        }
    )


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
    working_plan.pop("_option_edit_bases", None)
    working_plan.pop("_option_edit_initials", None)
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
    if not draft or int(draft.get("revision") or 1) != int(
        session.get("base_draft_revision") or 0
    ):
        raise _conflict("Draft revision is stale")
    pending_before = _products({"products": session.get("pending_products")})
    if session.get("surface") == "workbook":
        working_file_id = str(session.get("working_file_id") or "")
        working_file = ctx.supabase.file.get_file(working_file_id)
        if (
            not working_file
            or str(working_file.get("shop_domain") or "").strip().lower() != tenant
        ):
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
        raise _validation_failure(
            [error for error in existing_field_errors if isinstance(error, dict)]
        )
    working_plan = (
        session.get("working_plan")
        if isinstance(session.get("working_plan"), dict)
        else {}
    )
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
    plan_response = await create_mutation_plan(
        draft_id=draft_id, request=request, ctx=ctx
    )
    rebuilt_plan = plan_response["plan"]
    if session.get("surface") == "grid" and isinstance(
        session.get("working_plan"), dict
    ):
        working_plan = session["working_plan"]
        bases = working_plan.get("_option_edit_bases")
        affected_item_ids = (
            {
                str(state.get("item", {}).get("item_id") or "")
                for state in bases.values()
                if isinstance(bases, dict)
                and isinstance(state, dict)
                and isinstance(state.get("item"), dict)
            }
            if isinstance(bases, dict)
            else set()
        )
        working_by_source = {
            int(item["source_index"]): item
            for item in working_plan.get("items", [])
            if isinstance(item, dict)
            and isinstance(item.get("source_index"), int)
            and str(item.get("item_id") or "") in affected_item_ids
        }
        for index, fresh_item in enumerate(rebuilt_plan.get("items", [])):
            if not isinstance(fresh_item, dict) or not isinstance(
                fresh_item.get("source_index"), int
            ):
                continue
            source_index = int(fresh_item["source_index"])
            structural_item = working_by_source.get(source_index)
            if not isinstance(structural_item, dict) or source_index >= len(products):
                continue
            rebuilt_plan["items"][index] = build_review_plan_item(
                item_id=str(fresh_item.get("item_id") or ""),
                currency_code=str(
                    fresh_item.get("currency_code")
                    or rebuilt_plan.get("currency_code")
                    or "USD"
                ),
                source_index=source_index,
                source_product=products[source_index],
                live_snapshot=(
                    fresh_item.get("live_snapshot")
                    if isinstance(fresh_item.get("live_snapshot"), dict)
                    else None
                ),
                proposed=(
                    structural_item.get("proposed")
                    if isinstance(structural_item.get("proposed"), dict)
                    else {}
                ),
                operation=str(fresh_item.get("operation") or "update"),
                blockers=(
                    structural_item.get("blockers")
                    if isinstance(structural_item.get("blockers"), list)
                    else []
                ),
                unapplied=(
                    structural_item.get("unapplied")
                    if isinstance(structural_item.get("unapplied"), list)
                    else []
                ),
                prior_item=structural_item,
            )
        rebuilt_plan = _preserve_working_selection(
            rebuilt_plan, session["working_plan"]
        )
        _service(ctx).save_mutation_plan(rebuilt_plan)
    applied = _service(ctx).save_review_edit_session(
        {**session, "status": "applied", "updated_at": _now()}
    )
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
        session = _service(ctx).save_review_edit_session(
            {**session, "status": "discarded", "updated_at": _now()}
        )
        if (
            isinstance(session.get("working_file_id"), str)
            and session["working_file_id"]
        ):
            ctx.supabase.file.delete_file(session["working_file_id"])
    return {"session": session}
