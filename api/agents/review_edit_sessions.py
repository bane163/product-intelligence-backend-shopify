"""Recoverable, tenant-scoped editing for the consolidated import review."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import uuid
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from app_context import AppContext, get_ctx
from application.domain.review_edit_history import CONTENT_HISTORY_OPERATIONS
from application.domain.shopify_mutation_plan import (
    OptionValueEditError,
    _normalized_path_part,
    _review_row as _domain_review_row,
    _schema_from_product,
    _unique_variant_target,
    _variants,
    apply_option_value_operation,
    build_review_plan_item,
    infer_positional_option_dimensions,
    merge_structured_product,
    normalize_review_plan_rows,
    preserve_blank_fields,
    validate_product,
)
from application.domain.extraction_quality import (
    QUALITY_STATUS_LIVE_PENDING,
    build_quality_report,
    issues_for_product,
)
from .mutation_plans import (
    REVIEW_REQUIRED,
    UNSUPPORTED_REVIEW_CONTRACT,
    VARIANTS_BY_SKU_QUERY,
    _offline_client,
    create_mutation_plan,
)
from .utils import require_authenticated_shop_domain

router = APIRouter()
LOG = logging.getLogger(__name__)
REVIEW_FIELD_VALIDATION_FAILED = "REVIEW_FIELD_VALIDATION_FAILED"
HANDLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
APPLY_CHECKPOINT_KEY = "_apply_checkpoint"


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


def _products_fingerprint(products: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        products,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _apply_checkpoint(
    *,
    base_revision: int,
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "base_draft_revision": base_revision,
        "target_draft_revision": base_revision + 1,
        "products_fingerprint": _products_fingerprint(products),
    }


def _is_expected_partial_apply(
    session: dict[str, Any],
    draft: dict[str, Any],
    products: list[dict[str, Any]],
) -> bool:
    working_plan = session.get("working_plan")
    checkpoint = (
        working_plan.get(APPLY_CHECKPOINT_KEY)
        if isinstance(working_plan, dict)
        else None
    )
    if not isinstance(checkpoint, dict):
        return False
    base_revision = int(session.get("base_draft_revision") or 0)
    return (
        int(draft.get("revision") or 0) == base_revision + 1
        and checkpoint.get("base_draft_revision") == base_revision
        and checkpoint.get("target_draft_revision") == base_revision + 1
        and checkpoint.get("products_fingerprint")
        == _products_fingerprint(products)
        == _products_fingerprint(_products(draft))
    )


def _mutation_plan_rebuild_payload(working_plan: dict[str, Any]) -> dict[str, Any]:
    inventory_context = working_plan.get("inventory_context")
    selected_location = (
        inventory_context.get("selected_location")
        if isinstance(inventory_context, dict)
        else None
    )
    location_id = (
        selected_location.get("id") if isinstance(selected_location, dict) else None
    )
    return {"inventory_location_id": location_id} if location_id else {}


def _require_review_v2(
    ctx: AppContext, draft_id: str, tenant: str
) -> dict[str, Any] | None:
    plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if isinstance(plan, dict) and plan.get("review_contract_version") not in {2, 3, 4, 5}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": UNSUPPORTED_REVIEW_CONTRACT,
                "message": "Regenerate this review plan before editing",
            },
        )
    return plan


def _field_error(row: dict[str, Any], code: str, message: str) -> dict[str, str]:
    return {
        "item_id": str(row.get("item_id") or ""),
        "change_id": str(row.get("change_id") or ""),
        "cell_id": str(row.get("cell_id") or ""),
        "value_path": str(row.get("value_path") or ""),
        "code": code,
        "message": message,
        "remedy": "edit",
    }


def _cell_field_error(
    item: dict[str, Any], cell: dict[str, Any], code: str, message: str
) -> dict[str, str]:
    return {
        "item_id": str(item.get("item_id") or cell.get("item_id") or ""),
        "change_id": str(cell.get("change_id") or ""),
        "cell_id": str(cell.get("cell_id") or ""),
        "value_path": str(cell.get("value_path") or ""),
        "code": code,
        "message": message,
        "remedy": "edit",
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

        if value is None or (isinstance(value, str) and not value.strip()):
            if schema.get("required"):
                raise ValueError("This field is required.")
            return None
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
            "This option list is malformed. Revert it or use the option editor."
        )
    if len(value) != len(before):
        raise ValueError("Add or remove option values with the option editor instead.")
    old_by_fold = {item.casefold(): item for item in before}
    new_by_fold = {item.casefold(): item for item in value}
    removed = [old_by_fold[key] for key in old_by_fold.keys() - new_by_fold.keys()]
    added = [new_by_fold[key] for key in new_by_fold.keys() - old_by_fold.keys()]
    if len(removed) != len(added):
        raise ValueError("Add or remove option values with the option editor instead.")
    if len(removed) > 1:
        raise ValueError("Rename one option value at a time.")
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
        identity = row.get("subject_identity")
        source_variant_index = (
            identity.get("source_variant_index")
            if isinstance(identity, dict)
            else None
        )
        variants = _variants(product)
        target = (
            variants[source_variant_index]
            if isinstance(source_variant_index, int)
            and 0 <= source_variant_index < len(variants)
            else _unique_variant_target(product, row)
        )
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
        target_field = aliases.get(field, field)
        if field == "inventory_quantity" and value is None:
            target.pop("inventory_quantity", None)
            target.pop("inventoryQuantity", None)
        else:
            target[target_field] = value
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


def _working_cell_match(
    plan: dict[str, Any], cell_id: str
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    matches: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for item in plan.get("items", []):
        if not isinstance(item, dict) or not isinstance(item.get("source_index"), int):
            continue
        preview = item.get("review_preview")
        if not isinstance(preview, dict):
            continue
        cells: list[Any] = []
        product = preview.get("product")
        if isinstance(product, dict) and isinstance(product.get("cells"), list):
            cells.extend(product["cells"])
        if isinstance(preview.get("metafield_cells"), list):
            cells.extend(preview["metafield_cells"])
        for variant in preview.get("variants", []):
            if isinstance(variant, dict) and isinstance(variant.get("cells"), list):
                cells.extend(variant["cells"])
        for cell in cells:
            if isinstance(cell, dict) and str(cell.get("cell_id") or "") == cell_id:
                matches.append((int(item["source_index"]), item, cell))
    if len(matches) != 1:
        raise _validation_failure(
            [{"change_id": "", "value_path": "", "code": "STALE_CELL_IDENTITY", "message": "This preview cell changed. Refresh and try again."}]
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
        "selection_intent": str(item.get("selection_intent") or "include"),
        "included_change_ids": [
            str(row["change_id"])
            for row in item.get("review_changes", [])
            if isinstance(row, dict) and row.get("change_id") and row.get("included")
        ],
    }


def _set_selection_state(item: dict[str, Any], state: dict[str, Any]) -> None:
    included_ids = set(state.get("included_change_ids") or [])
    item["selected"] = bool(state.get("selected"))
    intent = str(state.get("selection_intent") or "")
    if intent in {"include", "exclude"}:
        item["selection_intent"] = intent
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
        item["selection_intent"] = preserved["selection_intent"]
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


def _value_at_path(product: dict[str, Any], path: str) -> Any:
    match = re.fullmatch(r"variants\[(\d+)\]\.(.+)", path)
    if not match:
        return product.get(path)
    variants = _variants(product)
    index = int(match.group(1))
    if index >= len(variants):
        return None
    field = match.group(2)
    aliases = {
        "compareAtPrice": "compare_at_price",
        "inventoryPolicy": "inventory_policy",
        "taxCode": "tax_code",
    }
    return variants[index].get(field, variants[index].get(aliases.get(field, field)))


def _rebuild_working_preflight(
    products: list[dict[str, Any]], working_plan: dict[str, Any]
) -> dict[str, Any]:
    """Recompute repair authority from effective pending products.

    This deliberately never copies structural/quality blockers.  The only
    remote issue retained is an unchanged exact live-SKU collision; async route
    preflight refreshes those checks before apply.
    """
    if working_plan.get("review_contract_version") != 5:
        return deepcopy(working_plan)
    previous_by_source = {
        int(item["source_index"]): item
        for item in working_plan.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("source_index"), int)
    }
    for product in products:
        infer_positional_option_dimensions(product)
    quality_report = build_quality_report(
        products, status=QUALITY_STATUS_LIVE_PENDING
    )
    rebuilt_items: list[dict[str, Any]] = []
    for index, product in enumerate(products):
        prior = previous_by_source.get(index, {})
        live = prior.get("live_snapshot") if isinstance(prior.get("live_snapshot"), dict) else None
        operation = "update" if live else "create"
        blockers, unapplied = validate_product(product, creating=not bool(live))
        proposed = preserve_blank_fields(live or {}, product)
        proposed.pop("shopify_gid", None)
        proposed.pop("id", None)
        if live:
            proposed["id"] = live.get("id")
        elif not proposed.get("status"):
            proposed["status"] = "DRAFT"
        proposed, structural_blockers, structural_unapplied = merge_structured_product(
            live, product, proposed
        )
        blockers.extend(structural_blockers)
        unapplied.extend(structural_unapplied)
        quality_issues = issues_for_product(quality_report, index)
        blockers.extend(
            {
                "code": str(issue.get("code")),
                "field": issue.get("value_path"),
                "message": issue.get("message"),
                "remedies": [issue.get("remedy")],
            }
            for issue in quality_issues
            if issue.get("severity") == "blocker"
            and not any(
                existing.get("code") == issue.get("code")
                and existing.get("field") == issue.get("value_path")
                for existing in blockers if isinstance(existing, dict)
            )
        )
        # Keep an authoritative remote collision only while its exact SKU has
        # not changed.  A final async preflight is still required before apply.
        for issue in prior.get("quality_issues", []):
            if not isinstance(issue, dict) or issue.get("code") != "LIVE_SKU_COLLISION":
                continue
            path = str(issue.get("value_path") or "")
            preview = prior.get("review_preview") if isinstance(prior.get("review_preview"), dict) else {}
            prior_value = next(
                (
                    cell.get("proposed")
                    for variant in preview.get("variants", [])
                    if isinstance(variant, dict)
                    for cell in variant.get("cells", [])
                    if isinstance(cell, dict) and cell.get("value_path") == path
                ),
                None,
            )
            if prior_value is not None and _value_at_path(product, path) == prior_value:
                quality_issues.append(deepcopy(issue))
                blockers.append(
                    {
                        "code": "LIVE_SKU_COLLISION",
                        "field": path,
                        "message": issue.get("message"),
                        "remedies": ["edit", "skip"],
                    }
                )
        rebuilt = build_review_plan_item(
            item_id=str(prior.get("item_id") or uuid.uuid4()),
            currency_code=str(prior.get("currency_code") or working_plan.get("currency_code") or "USD"),
            source_index=index,
            source_product=product,
            live_snapshot=live,
            proposed=proposed,
            operation=operation,
            blockers=blockers,
            unapplied=unapplied,
            prior_item=prior,
        )
        rebuilt["quality_issues"] = quality_issues
        rebuilt_items.append(rebuilt)
    blocked = sum(bool(item.get("blockers")) for item in rebuilt_items)
    result = {
        **deepcopy(working_plan),
        "items": rebuilt_items,
        "quality_summary": {
            "product_count": len(rebuilt_items),
            "valid_product_count": len(rebuilt_items) - blocked,
            "blocked_product_count": blocked,
            "blocker_count": sum(len(item.get("blockers") or []) for item in rebuilt_items),
            "warning_count": sum(
                issue.get("severity") == "warning"
                for item in rebuilt_items
                for issue in item.get("quality_issues", [])
                if isinstance(issue, dict)
            ),
        },
    }
    return normalize_review_plan_rows(result)


def _blocking_field_errors(plan: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for item in plan.get("items", []):
        if not isinstance(item, dict):
            continue
        if item.get("selection_intent") == "exclude":
            continue
        preview = item.get("review_preview") if isinstance(item.get("review_preview"), dict) else {}
        cells = [
            cell
            for variant in preview.get("variants", [])
            if isinstance(variant, dict)
            for cell in variant.get("cells", [])
            if isinstance(cell, dict)
        ]
        cells.extend(
            cell
            for cell in (preview.get("product") or {}).get("cells", [])
            if isinstance(cell, dict)
        )
        for blocker in item.get("blockers", []):
            if not isinstance(blocker, dict):
                continue
            path = str(blocker.get("field") or "productOptions")
            cell = next((candidate for candidate in cells if candidate.get("value_path") == path), None)
            errors.append(
                {
                    "item_id": str(item.get("item_id") or ""),
                    "change_id": str((cell or {}).get("change_id") or ""),
                    "cell_id": str((cell or {}).get("cell_id") or ""),
                    "value_path": path,
                    "code": str(blocker.get("code") or "REVIEW_BLOCKER"),
                    "message": str(
                        blocker.get("message")
                        or "Correct this field before saving your changes."
                    ),
                    "remedy": str(
                        next(iter(blocker.get("remedies") or []), "edit")
                    ),
                }
            )
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for error in errors:
        unique.setdefault(
            (error["item_id"], error["code"], error["value_path"]), error
        )
    return list(unique.values())


async def _refresh_live_sku_collisions(
    products: list[dict[str, Any]], plan: dict[str, Any], tenant: str
) -> dict[str, Any]:
    """Refresh exact Shopify SKU conflicts for a real v5 server-authored plan."""
    if plan.get("review_contract_version") != 5 or not plan.get("shop_domain"):
        return plan
    result = deepcopy(plan)
    client = await _offline_client(tenant)
    cache: dict[str, list[dict[str, Any]]] = {}
    try:
        for item in result.get("items", []):
            if not isinstance(item, dict) or not isinstance(item.get("source_index"), int):
                continue
            source_index = int(item["source_index"])
            if source_index >= len(products):
                continue
            live = item.get("live_snapshot") if isinstance(item.get("live_snapshot"), dict) else None
            for variant_index, variant in enumerate(_variants(products[source_index])):
                sku = str(variant.get("sku") or "").strip()
                if not sku:
                    continue
                folded = sku.casefold()
                if folded not in cache:
                    response = await client.graphql(
                        VARIANTS_BY_SKU_QUERY, {"query": f'sku:"{sku}"'}
                    )
                    cache[folded] = [
                        node
                        for node in response.get("data", {}).get("productVariants", {}).get("nodes", [])
                        if isinstance(node, dict)
                    ]
                collision = any(
                    str(node.get("sku") or "").strip().casefold() == folded
                    and (
                        not live
                        or not isinstance(node.get("product"), dict)
                        or node["product"].get("id") != live.get("id")
                    )
                    for node in cache[folded]
                )
                if not collision:
                    continue
                path = f"variants[{variant_index}].sku"
                issue = {
                    "issue_id": f"live_sku_collision:product-{source_index}-{path}",
                    "severity": "blocker",
                    "code": "LIVE_SKU_COLLISION",
                    "product_index": source_index,
                    "value_path": path,
                    "message": "This SKU is already used by another Shopify product.",
                    "remedy": "edit",
                }
                item.setdefault("quality_issues", []).append(issue)
                item.setdefault("blockers", []).append(
                    {
                        "code": "LIVE_SKU_COLLISION",
                        "field": path,
                        "message": issue["message"],
                        "remedies": ["edit", "skip"],
                    }
                )
                item["operation"] = "blocked"
                item["selectable"] = False
                item["selected"] = False
                for preview_variant in (item.get("review_preview") or {}).get("variants", []):
                    for cell in preview_variant.get("cells", []) if isinstance(preview_variant, dict) else []:
                        if isinstance(cell, dict) and cell.get("value_path") == path:
                            cell["disposition"] = "blocked"
                            cell.setdefault("quality_issues", []).append(issue)
    finally:
        await client.close()
    blocked = sum(bool(item.get("blockers")) for item in result.get("items", []) if isinstance(item, dict))
    summary = result.get("quality_summary") if isinstance(result.get("quality_summary"), dict) else {}
    summary.update(
        {
            "product_count": len(result.get("items", [])),
            "valid_product_count": len(result.get("items", [])) - blocked,
            "blocked_product_count": blocked,
            "blocker_count": sum(
                len(item.get("blockers") or [])
                for item in result.get("items", []) if isinstance(item, dict)
            ),
        }
    )
    result["quality_summary"] = summary
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
    rebuilt["selected"] = bool(item.get("selected"))
    rebuilt["selectable"] = bool(item.get("selectable"))
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


def _dimension_id(item: dict[str, Any], option: dict[str, Any]) -> str | None:
    preview = item.get("review_preview")
    dimensions = preview.get("option_dimensions") if isinstance(preview, dict) else []
    for dimension in dimensions if isinstance(dimensions, list) else []:
        if isinstance(dimension, dict) and str(dimension.get("name") or "").casefold() == str(option.get("name") or "").casefold():
            return str(dimension.get("dimension_id") or "")
    return None


def _option_records(product: dict[str, Any]) -> list[dict[str, Any]]:
    raw = product.get("productOptions") if isinstance(product.get("productOptions"), list) else product.get("options")
    result = deepcopy([value for value in raw if isinstance(value, dict)]) if isinstance(raw, list) else []
    if len(result) == 1 and str(result[0].get("name") or "").strip().casefold() == "title":
        values = result[0].get("values")
        names = [str(value.get("name") if isinstance(value, dict) else value).strip().casefold() for value in values] if isinstance(values, list) else []
        if names in ([], ["default title"]):
            return []
    return result


def _set_option_records(product: dict[str, Any], options: list[dict[str, Any]]) -> None:
    product.pop("options", None)
    product["productOptions"] = [
        {**option, "position": index + 1}
        for index, option in enumerate(options)
    ]


def _variant_option_map(variant: dict[str, Any]) -> dict[str, str]:
    raw = variant.get("optionValues") or variant.get("selectedOptions") or []
    result: dict[str, str] = {}
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("optionName") or entry.get("option_name") or (entry.get("name") if "value" in entry else "")).strip()
        value = str(entry.get("value") if "value" in entry else entry.get("name") or "").strip()
        if name and value:
            result[name.casefold()] = value
    return result


def _set_variant_options(variant: dict[str, Any], options: list[dict[str, Any]], values: dict[str, str]) -> None:
    variant.pop("selectedOptions", None)
    variant.pop("selected_options", None)
    variant.pop("option_values", None)
    for key in ("option1", "option2", "option3"):
        variant.pop(key, None)
    if options:
        variant["optionValues"] = [
            {"optionName": str(option["name"]), "name": values[str(option["name"]).casefold()]}
            for option in options
        ]
    else:
        variant["optionValues"] = [{"optionName": "Title", "name": "Default Title"}]


def _validate_structural_product(product: dict[str, Any]) -> None:
    options = _option_records(product)
    names = [str(option.get("name") or "").strip() for option in options]
    if len(options) > 3:
        raise ValueError("Shopify supports at most three option dimensions.")
    if any(not name for name in names) or len({name.casefold() for name in names}) != len(names):
        raise ValueError("Option names must be nonblank and unique.")
    declared: dict[str, set[str]] = {}
    for option in options:
        name = str(option.get("name") or "")
        values = [str(value.get("name") if isinstance(value, dict) else value).strip() for value in option.get("values", [])]
        if not values or any(not value for value in values) or len({value.casefold() for value in values}) != len(values):
            raise ValueError(f"{name} values must be nonblank and unique.")
        declared[name.casefold()] = {value.casefold() for value in values}
    variants = _variants(product)
    if not variants:
        raise ValueError("A product must have at least one variant.")
    if len(variants) > 2048:
        raise ValueError("This product exceeds Shopify's 2,048 variant limit.")
    combos: set[tuple[str, ...]] = set()
    skus: set[str] = set()
    for variant in variants:
        values = _variant_option_map(variant)
        combo = tuple(values.get(name.casefold(), "").casefold() for name in names)
        if options and (not all(combo) or any(combo[index] not in declared[names[index].casefold()] for index in range(len(names)))):
            raise ValueError("Every variant must use one declared value for each option.")
        if combo in combos:
            raise ValueError("Variant option combinations must be unique.")
        combos.add(combo)
        sku = str(variant.get("sku") or "").strip().casefold()
        if sku and sku in skus:
            raise ValueError("Variant SKUs must be unique.")
        if sku:
            skus.add(sku)


def _transform_structure(product: dict[str, Any], item: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(product)
    options = _option_records(result)
    variants = _variants(result) or [{}]
    operation_type = str(operation.get("operation") or operation.get("type") or "")
    dimension_id = str(operation.get("dimension_id") or "")
    option_index = next((index for index, option in enumerate(options) if _dimension_id(item, option) == dimension_id), None)

    if operation_type == "option_dimension_add":
        name = str(operation.get("name") or "").strip()
        value = str(operation.get("value") or "").strip()
        if not name or not value:
            raise ValueError("Enter a nonblank option name and value.")
        if len(options) >= 3 or any(str(option.get("name") or "").casefold() == name.casefold() for option in options):
            raise ValueError("Option dimensions must be unique and limited to three.")
        options.append({"name": name, "values": [{"name": value}]})
        for variant in variants:
            values = _variant_option_map(variant)
            values[name.casefold()] = value
            _set_variant_options(variant, options, values)
    elif operation_type == "option_dimension_rename":
        name = str(operation.get("name") or "").strip()
        repair_dimensions = (
            result.get("_review_option_dimensions")
            if isinstance(result.get("_review_option_dimensions"), list)
            else []
        )
        unresolved_index = next(
            (
                index for index, dimension in enumerate(repair_dimensions)
                if isinstance(dimension, dict)
                and str(dimension.get("dimension_id") or "") == dimension_id
            ),
            None,
        )
        if option_index is None and unresolved_index is None:
            raise ValueError("This option changed. Refresh before editing it.")
        if unresolved_index is not None:
            other_names = {
                str(dimension.get("name") or "").strip().casefold()
                for index, dimension in enumerate(repair_dimensions)
                if isinstance(dimension, dict) and index != unresolved_index
            }
            if not name or name.casefold() in other_names:
                raise ValueError("Option names must be nonblank and unique.")
            repair_dimensions[unresolved_index]["name"] = name
            repair_dimensions[unresolved_index]["origin"] = "merchant_edited"
            repair_dimensions[unresolved_index]["requires_name"] = False
            result["_review_option_dimensions"] = repair_dimensions
            if any(
                not str(dimension.get("name") or "").strip()
                for dimension in repair_dimensions if isinstance(dimension, dict)
            ):
                return result
            options = [
                {
                    "name": str(dimension["name"]),
                    "position": int(dimension.get("position") or index + 1),
                    "values": [{"name": str(value)} for value in dimension.get("values", [])],
                }
                for index, dimension in enumerate(repair_dimensions)
                if isinstance(dimension, dict)
            ]
            for variant in variants:
                positional = {
                    str(option["name"]).casefold(): str(
                        variant.get(f"option{position + 1}") or ""
                    ).strip()
                    for position, option in enumerate(options)
                }
                _set_variant_options(variant, options, positional)
        else:
            assert option_index is not None
            if not name or any(index != option_index and str(option.get("name") or "").casefold() == name.casefold() for index, option in enumerate(options)):
                raise ValueError("Option names must be nonblank and unique.")
            old = str(options[option_index].get("name") or "")
            options[option_index]["name"] = name
            for variant in variants:
                values = _variant_option_map(variant)
                values[name.casefold()] = values.pop(old.casefold(), "")
                _set_variant_options(variant, options, values)
    elif operation_type == "option_dimension_reorder":
        ids = operation.get("dimension_ids")
        if not isinstance(ids, list) or len(ids) != len(options) or set(map(str, ids)) != {str(_dimension_id(item, option)) for option in options}:
            raise ValueError("Reorder must include every current option exactly once.")
        by_id = {str(_dimension_id(item, option)): option for option in options}
        options = [by_id[str(value)] for value in ids]
        for variant in variants:
            _set_variant_options(variant, options, _variant_option_map(variant))
    elif operation_type == "option_dimension_remove":
        if option_index is None:
            raise ValueError("This option changed. Refresh before removing it.")
        if item.get("live_snapshot") and not operation.get("confirm_live_deletion"):
            raise ValueError("Confirm removal because it can delete live Shopify variants.")
        removed = str(options[option_index].get("name") or "")
        options.pop(option_index)
        seen: set[tuple[str, ...]] = set()
        kept: list[dict[str, Any]] = []
        for variant in variants:
            values = _variant_option_map(variant)
            values.pop(removed.casefold(), None)
            combo = tuple(values.get(str(option.get("name") or "").casefold(), "").casefold() for option in options)
            if combo in seen:
                raise ValueError("Removing this option would create duplicate variants.")
            seen.add(combo)
            _set_variant_options(variant, options, values)
            kept.append(variant)
        variants = kept
    elif operation_type == "variant_add":
        requested = operation.get("option_values")
        if not isinstance(requested, dict) or set(key.casefold() for key in requested) != {str(option.get("name") or "").casefold() for option in options}:
            raise ValueError("Choose one value for every option.")
        values = {str(key).casefold(): str(value).strip() for key, value in requested.items()}
        new_variant: dict[str, Any] = {"sku": "", "price": None, "barcode": None, "inventory_quantity": None}
        _set_variant_options(new_variant, options, values)
        variants.append(new_variant)
    elif operation_type == "variant_remove":
        if len(variants) <= 1:
            raise ValueError("The final variant cannot be removed.")
        identity = operation.get("subject_identity")
        if not isinstance(identity, dict) or not isinstance(identity.get("values"), list):
            raise ValueError("This variant identity is stale. Refresh and try again.")
        expected = tuple(str(value).casefold() for value in identity["values"])
        match_index = next((index for index, variant in enumerate(variants) if tuple(value for value in _canonical_variant_values(variant, options)) == expected), None)
        if match_index is None:
            raise ValueError("This variant identity is stale. Refresh and try again.")
        preview_variants = (item.get("review_preview") or {}).get("variants", [])
        is_live = any(isinstance(row, dict) and row.get("subject_identity") == identity and row.get("live_variant") for row in preview_variants)
        if is_live and not operation.get("confirm_live_deletion"):
            raise ValueError("Confirm removal of this live Shopify variant.")
        variants.pop(match_index)
    else:
        raise ValueError("This structural operation is not supported.")
    _set_option_records(result, options)
    result["variants"] = variants
    _validate_structural_product(result)
    return result


def _canonical_variant_values(variant: dict[str, Any], options: list[dict[str, Any]]) -> tuple[str, ...]:
    values = _variant_option_map(variant)
    return tuple(values.get(str(option.get("name") or "").casefold(), "").casefold() for option in options)


def _apply_structure_operation(
    products: list[dict[str, Any]], working_plan: dict[str, Any], operation: dict[str, Any],
    history: list[dict[str, Any]], history_cursor: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    item_id = str(operation.get("item_id") or "")
    item = _working_items(working_plan).get(item_id)
    if not isinstance(item, dict) or not isinstance(item.get("source_index"), int):
        raise _validation_failure([{"change_id": "", "value_path": "product", "code": "STALE_PRODUCT_IDENTITY", "message": "This product changed. Refresh and try again."}])
    product_index = int(item["source_index"])
    if product_index >= len(products):
        raise _conflict("This product is no longer available")
    before = _structural_state(products[product_index], item)
    try:
        edited_product = _transform_structure(products[product_index], item, operation)
        edited_proposed = deepcopy(
            item.get("proposed") if isinstance(item.get("proposed"), dict) else {}
        )
    except ValueError as exc:
        raise _validation_failure([{"change_id": "", "value_path": "productOptions", "code": "INVALID_PRODUCT_STRUCTURE", "message": str(exc)}]) from None
    rebuilt = build_review_plan_item(
        item_id=item_id, currency_code=str(item.get("currency_code") or "USD"),
        source_index=product_index, source_product=edited_product,
        live_snapshot=item.get("live_snapshot") if isinstance(item.get("live_snapshot"), dict) else None,
        proposed=edited_proposed, operation=str(item.get("operation") or "update"),
        blockers=item.get("blockers") if isinstance(item.get("blockers"), list) else [],
        unapplied=item.get("unapplied") if isinstance(item.get("unapplied"), list) else [], prior_item=item,
    )
    rebuilt["quality_issues"] = deepcopy(item.get("quality_issues") or [])
    rebuilt["selected"] = bool(item.get("selected"))
    rebuilt["selectable"] = bool(item.get("selectable"))
    rebuilt = normalize_review_plan_rows({"review_contract_version": 5, "items": [rebuilt]})["items"][0]
    products[product_index] = edited_product
    _replace_working_item(working_plan, item_id, rebuilt)
    after = _structural_state(edited_product, rebuilt)
    operation_type = str(operation.get("operation") or operation.get("type"))
    durable = deepcopy(history[:history_cursor])
    durable.append(_history_entry(operation_type, before, after, item_id=item_id, product_index=product_index, structural=True, payload=deepcopy(operation)))
    return products, durable, len(durable)


def _apply_cell_operation(
    products: list[dict[str, Any]], working_plan: dict[str, Any], operation: dict[str, Any],
    history: list[dict[str, Any]], history_cursor: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    operation_type = str(operation.get("operation") or operation.get("type") or "")
    cell_id = str(operation.get("cell_id") or "")
    product_index, item, cell = _working_cell_match(working_plan, cell_id)
    if str(operation.get("value_path") or "") != str(cell.get("value_path") or ""):
        raise _validation_failure([_cell_field_error(item, cell, "STALE_VALUE_PATH", "This preview cell changed. Refresh and try again.")])
    if cell.get("editable") is not True or product_index >= len(products):
        raise _validation_failure([_cell_field_error(item, cell, "REVIEW_FIELD_READ_ONLY", str(cell.get("read_only_reason") or "This preview cell is read-only."))])
    try:
        value = deepcopy(cell.get("original_proposed")) if operation_type == "cell_revert" else _canonical_review_value(cell, operation.get("value"))
    except ValueError as exc:
        raise _validation_failure([_cell_field_error(item, cell, "INVALID_REVIEW_VALUE", str(exc))]) from None
    before = _structural_state(products[product_index], item)
    prior_row = next(
        (
            row
            for row in item.get("review_changes", [])
            if isinstance(row, dict)
            and cell.get("change_id")
            and row.get("change_id") == cell.get("change_id")
        ),
        None,
    )
    original_proposed = deepcopy(
        prior_row.get("original_proposed", cell.get("original_proposed"))
        if isinstance(prior_row, dict)
        else cell.get("original_proposed")
    )
    original_origin = str(
        prior_row.get("original_origin", cell.get("origin", "unknown"))
        if isinstance(prior_row, dict)
        else cell.get("origin", "unknown")
    )
    original_source_ref = deepcopy(
        prior_row.get("original_source_ref", cell.get("source_ref"))
        if isinstance(prior_row, dict)
        else cell.get("source_ref")
    )
    original_source_refs = deepcopy(
        prior_row.get("original_source_refs", cell.get("source_refs") or [])
        if isinstance(prior_row, dict)
        else cell.get("source_refs") or []
    )
    edit_origin = original_origin if operation_type == "cell_revert" else "merchant_edited"
    edited_product = deepcopy(products[product_index])
    edited_proposed = deepcopy(item.get("proposed") if isinstance(item.get("proposed"), dict) else {})
    try:
        _set_product_value(edited_product, cell, value, origin=edit_origin)
        identity = cell.get("subject_identity")
        if not (
            isinstance(identity, dict)
            and isinstance(identity.get("source_variant_index"), int)
        ):
            _set_product_value(edited_proposed, cell, value, origin=edit_origin)
    except (ValueError, HTTPException):
        raise _validation_failure([_cell_field_error(item, cell, "STALE_CELL_IDENTITY", "This preview cell no longer has a unique target.")]) from None
    rebuilt = build_review_plan_item(
        item_id=str(item.get("item_id") or ""), currency_code=str(item.get("currency_code") or "USD"),
        source_index=product_index, source_product=edited_product,
        live_snapshot=item.get("live_snapshot") if isinstance(item.get("live_snapshot"), dict) else None,
        proposed=edited_proposed, operation=str(item.get("operation") or "update"),
        blockers=item.get("blockers") if isinstance(item.get("blockers"), list) else [],
        unapplied=item.get("unapplied") if isinstance(item.get("unapplied"), list) else [], prior_item=item,
    )
    rebuilt_row = next(
        (
            row
            for row in rebuilt.get("review_changes", [])
            if isinstance(row, dict)
            and row.get("value_path") == cell.get("value_path")
            and row.get("field") == cell.get("field")
            and row.get("subject_identity") == cell.get("subject_identity")
        ),
        None,
    )
    if isinstance(rebuilt_row, dict):
        rebuilt_row["original_proposed"] = original_proposed
        rebuilt_row["original_origin"] = original_origin
        rebuilt_row["original_source_ref"] = original_source_ref
        rebuilt_row["original_source_refs"] = original_source_refs
        rebuilt_row["origin"] = edit_origin
        if operation_type == "cell_revert" and isinstance(original_source_ref, dict):
            rebuilt_row["source_ref"] = deepcopy(original_source_ref)
        else:
            rebuilt_row.pop("source_ref", None)
        if operation_type == "cell_revert" and original_source_refs:
            rebuilt_row["source_refs"] = deepcopy(original_source_refs)
        elif operation_type != "cell_revert":
            rebuilt_row["source_refs"] = [
                deepcopy(ref)
                for ref in rebuilt_row.get("source_refs", [])
                if isinstance(ref, dict)
                and ref.get("evidence_role", "value") in {"label", "key"}
            ]
    rebuilt["quality_issues"] = deepcopy(item.get("quality_issues") or [])
    rebuilt["selected"] = bool(item.get("selected"))
    rebuilt["selectable"] = bool(item.get("selectable"))
    rebuilt = normalize_review_plan_rows({"review_contract_version": 5, "items": [rebuilt]})["items"][0]
    products[product_index] = edited_product
    _replace_working_item(working_plan, str(item.get("item_id") or ""), rebuilt)
    after = _structural_state(edited_product, rebuilt)
    durable = deepcopy(history[:history_cursor])
    durable.append(_history_entry(operation_type, before, after, item_id=str(item.get("item_id") or ""), product_index=product_index, cell_id=cell_id, value_path=cell.get("value_path"), structural=True))
    return products, durable, len(durable)


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
        after = {
            "selected": selected,
            "selection_intent": "include" if selected else "exclude",
            "included_change_ids": included_ids,
        }
        _set_selection_state(item, after)
        return _history_entry("selection", before, after, item_id=item_id)

    if operation_type == "filters":
        value = operation.get("filterValues")
        if not isinstance(value, dict):
            raise HTTPException(
                status_code=400, detail="filterValues must be an object"
            )
        status = value.get("productStatus")
        after = {"productStatus": status if status in {"all", "ready", "warning", "needs_correction", "excluded"} else "all"}
        grid_state["filterValues"] = after
        return None

    if operation_type == "expansion":
        expanded_ids = _validate_string_list(operation.get("expandedItemIds"), "expandedItemIds")
        valid_expansion_ids = {
            (
                f"product:{item.get('source_index')}"
                if isinstance(item.get("source_index"), int)
                and not isinstance(item.get("source_index"), bool)
                else str(item.get("item_id") or "")
            )
            for item in _working_items(working_plan).values()
        }
        unknown = set(expanded_ids) - valid_expansion_ids
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown expanded product: {sorted(unknown)[0]}")
        grid_state["expandedItemIds"] = list(dict.fromkeys(expanded_ids))
        grid_state["expansionInitialized"] = True
        return None

    if operation_type == "pagination":
        after = _validated_pages(operation.get("pagesByItem"))
        grid_state["pagesByItem"] = after
        product_page = operation.get("productPage", 0)
        if not isinstance(product_page, int) or isinstance(product_page, bool) or product_page < 0:
            raise HTTPException(status_code=400, detail="productPage must be a non-negative integer")
        grid_state["productPage"] = product_page
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
    if operation in {
        "cell_edit", "cell_revert",
        "option_values_edit", "option_dimension_add", "option_dimension_rename",
        "option_dimension_reorder", "option_dimension_remove", "variant_add", "variant_remove",
    } or (
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
        if operation_type in {"cell_edit", "cell_revert"}:
            products, durable_history, history_cursor = _apply_cell_operation(
                products, working_plan, operation, durable_history, history_cursor
            )
        elif operation_type in {
            "option_dimension_add", "option_dimension_rename", "option_dimension_reorder",
            "option_dimension_remove", "variant_add", "variant_remove",
        }:
            products, durable_history, history_cursor = _apply_structure_operation(
                products, working_plan, operation, durable_history, history_cursor
            )
        elif operation_type == "option_values_edit":
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
    if working_plan.get("review_contract_version") == 5:
        refreshed = normalize_review_plan_rows(working_plan)
        working_plan["items"] = refreshed.get("items", working_plan.get("items", []))
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
    if surface != "grid":
        raise HTTPException(
            status_code=410,
            detail={
                "code": "WORKBOOK_REVIEW_RETIRED",
                "message": "Edit products directly in the Shopify preview.",
            },
        )
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
    if session.get("status") != "active" or session.get("surface") != "grid":
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
                in {
                    "option_values_edit", "option_dimension_add", "option_dimension_rename",
                    "option_dimension_reorder", "option_dimension_remove", "variant_add", "variant_remove",
                }
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
    if latest_plan.get("review_contract_version") not in {2, 3, 4, 5}:
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
            "filterValues": {"productStatus": "all"},
            "expandedItemIds": [],
            "expansionInitialized": False,
            "pagesByItem": {},
            "productPage": 0,
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
    if any(
        (operation.get("operation") or operation.get("type"))
        in CONTENT_HISTORY_OPERATIONS - {"selection"}
        for operation in operations
        if isinstance(operation, dict)
    ):
        working_plan = _rebuild_working_preflight(pending, working_plan)
        working_plan = await _refresh_live_sku_collisions(
            pending, working_plan, tenant
        )
    saved = _service(ctx).save_review_edit_session(
        {
            **session,
            "pending_products": pending,
            "working_plan": working_plan,
            "grid_state": grid_state,
            "history": history,
            "history_cursor": history_cursor,
            "field_errors": (
                _blocking_field_errors(working_plan)
                if working_plan.get("review_contract_version") == 5
                else []
            ),
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
            "field_errors": (
                _blocking_field_errors(working_plan)
                if working_plan.get("review_contract_version") == 5
                else []
            ),
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
    working_plan = _rebuild_working_preflight(pending, working_plan)
    working_plan = await _refresh_live_sku_collisions(pending, working_plan, tenant)
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
    working_plan = _rebuild_working_preflight(pending, working_plan)
    working_plan = await _refresh_live_sku_collisions(pending, working_plan, tenant)
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
    working_plan = _rebuild_working_preflight(pending, working_plan)
    working_plan = await _refresh_live_sku_collisions(pending, working_plan, tenant)
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
    if not isinstance(draft, dict):
        raise _conflict("Draft revision is stale")
    pending_before = _products({"products": session.get("pending_products")})
    working_plan = (
        session.get("working_plan")
        if isinstance(session.get("working_plan"), dict)
        else {}
    )
    base_revision = int(session.get("base_draft_revision") or 0)
    current_revision = int(draft.get("revision") or 1)
    resuming_partial_apply = _is_expected_partial_apply(
        session, draft, pending_before
    )
    if current_revision != base_revision and not resuming_partial_apply:
        raise _conflict("Draft revision is stale")
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
    working_plan = _rebuild_working_preflight(products, working_plan)
    working_plan = await _refresh_live_sku_collisions(products, working_plan, tenant)
    blocker_errors = (
        _blocking_field_errors(working_plan)
        if working_plan.get("review_contract_version") == 5
        else []
    )
    if blocker_errors:
        _service(ctx).save_review_edit_session(
            {
                **session,
                "pending_products": products,
                "working_plan": working_plan,
                "field_errors": blocker_errors,
                "updated_at": _now(),
            }
        )
        raise _validation_failure(blocker_errors)
    if resuming_partial_apply:
        new_revision = current_revision
        saved_draft = draft
    else:
        new_revision = base_revision + 1
        working_plan[APPLY_CHECKPOINT_KEY] = _apply_checkpoint(
            base_revision=base_revision,
            products=products,
        )
        session = _service(ctx).save_review_edit_session(
            {**session, "working_plan": working_plan, "updated_at": _now()}
        )
        saved_draft = ctx.supabase.drafts.save_product_draft(
            draft_id=draft_id,
            run_id=draft.get("run_id"),
            import_mode=str(draft.get("import_mode") or "auto"),
            draft_name=draft.get("draft_name"),
            shop_domain=tenant,
            input_file_id=draft.get("input_file_id"),
            input_filename=draft.get("input_filename"),
            extraction_status=draft.get("extraction_status"),
            extraction_run_id=draft.get("extraction_run_id"),
            extraction_error=draft.get("extraction_error"),
            products=products,
            revision=new_revision,
        )
    latest_plan = _service(ctx).get_latest_mutation_plan(draft_id, shop_domain=tenant)
    if (
        resuming_partial_apply
        and isinstance(latest_plan, dict)
        and int(latest_plan.get("draft_revision") or 0) == new_revision
    ):
        rebuilt_plan = latest_plan
    else:
        plan_response = await create_mutation_plan(
            draft_id=draft_id,
            request=request,
            payload=_mutation_plan_rebuild_payload(working_plan),
            ctx=ctx,
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
    cleaned_working_plan = deepcopy(session.get("working_plan") or {})
    cleaned_working_plan.pop(APPLY_CHECKPOINT_KEY, None)
    applied = _service(ctx).save_review_edit_session(
        {
            **session,
            "working_plan": cleaned_working_plan,
            "status": "applied",
            "updated_at": _now(),
        }
    )
    return {
        "session": applied,
        "draft": saved_draft,
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
    return {"session": session}
