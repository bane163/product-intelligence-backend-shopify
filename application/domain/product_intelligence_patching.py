from __future__ import annotations

import re
from typing import Any

from application.domain.review_value_paths import (
    canonical_value_path,
    is_numeric_field,
    iter_review_values,
    values_equal,
)

SUPPORTED_SUGGESTION_FIELDS = {
    "title",
    "handle",
    "body_html",
    "vendor",
    "product_type",
    "product_category",
    "status",
    "seo_title",
    "seo_description",
    "tags",
    "metafields",
}

VARIANT_OPERATIONS_FIELD = "_variant_operations"

_SCALAR_ORIGIN_PATHS = {
    "body_html": "descriptionHtml",
    "product_type": "productType",
    "product_category": "productType",
    "seo_title": "seo.title",
    "seo_description": "seo.description",
}


def _normalized_path_part(value: Any) -> str:
    return (
        re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().casefold()).strip("-")
        or "value"
    )


def _variant_subject_path(variant: dict[str, Any]) -> str:
    option_values = variant.get("option_values")
    parts: list[str] = []
    if isinstance(option_values, list):
        for item in option_values:
            if not isinstance(item, dict):
                continue
            name = str(item.get("option_name") or "").strip()
            value = str(item.get("name") or "").strip()
            if name and value:
                parts.append(f"{name}: {value}")
    return _normalized_path_part(" / ".join(parts))


def _tag_tokens(value: Any) -> list[str] | None:
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, list):
        raw = value
    else:
        return None
    return sorted(
        str(item).strip().casefold() for item in raw if str(item).strip()
    )


def _equivalent(field: str, existing: Any, incoming: Any) -> bool:
    if field == "tags":
        left, right = _tag_tokens(existing), _tag_tokens(incoming)
        return left is not None and right is not None and left == right
    return values_equal(
        existing,
        incoming,
        numeric_strings=is_numeric_field(field),
    )


def _scalar_existing_value(product: dict[str, Any], field: str) -> tuple[bool, Any]:
    aliases = {
        "body_html": ("descriptionHtml", "body_html", "bodyHtml", "description"),
        "product_type": ("productType", "product_type", "product_category"),
        "product_category": ("productType", "product_category", "product_type"),
        "seo_title": ("seo_title", "seoTitle", "meta_title"),
        "seo_description": ("seo_description", "seoDescription", "meta_description"),
    }.get(field, (field,))
    for alias in aliases:
        if alias in product:
            return True, product.get(alias)
    if field in {"seo_title", "seo_description"}:
        seo = product.get("seo")
        key = "title" if field == "seo_title" else "description"
        if isinstance(seo, dict) and key in seo:
            return True, seo.get(key)
    return False, None


def _existing_review_values(product: dict[str, Any]) -> dict[str, Any]:
    return {
        canonical_value_path(item.value_path): item.value
        for item in iter_review_values(product)
    }


def _changed_patch_origin_paths(
    *,
    product: dict[str, Any],
    patch_payload: dict[str, Any],
    variant_operations: dict[str, Any],
) -> set[str]:
    changed: set[str] = set()
    for field in SUPPORTED_SUGGESTION_FIELDS - {"metafields"}:
        incoming = patch_payload.get(field)
        if field not in patch_payload or incoming is None:
            continue
        found, existing = _scalar_existing_value(product, field)
        if not found or not _equivalent(field, existing, incoming):
            changed.add(_SCALAR_ORIGIN_PATHS.get(field, field))

    existing_metafields = {
        (item["namespace"], item["key"]): item
        for item in _normalize_metafields_payload(product.get("metafields"))
    }
    for metafield in _normalize_metafields_payload(patch_payload.get("metafields")):
        subject = _normalized_path_part(
            f"{metafield['namespace']}.{metafield['key']}"
        )
        existing = existing_metafields.get((metafield["namespace"], metafield["key"]))
        if existing is None or not _equivalent("value", existing.get("value"), metafield["value"]):
            changed.add(f"metafield[{subject}].value")
        if "type" in metafield and (
            existing is None
            or not _equivalent("type", existing.get("type"), metafield["type"])
        ):
            changed.add(f"metafield[{subject}].type")

    existing_values = _existing_review_values(product)
    for option in variant_operations.get("create_options", []):
        if isinstance(option, dict):
            subject = _normalized_path_part(option.get("name"))
            path = f"option[{subject}].values"
            existing = existing_values.get(canonical_value_path(path))
            if existing is None or not _equivalent("tags", existing, option.get("values")):
                changed.add(path)

    for variant in variant_operations.get("create_variants", []):
        if not isinstance(variant, dict):
            continue
        subject = _variant_subject_path(variant)
        for field in ("sku", "price"):
            if field in variant:
                path = f"variant[{subject}].{field}"
                existing = existing_values.get(canonical_value_path(path))
                if existing is None or not _equivalent(field, existing, variant[field]):
                    changed.add(path)
        if "inventory_quantity" in variant:
            path = f"inventory[{subject}].inventory_quantity"
            existing = existing_values.get(canonical_value_path(path))
            if existing is None or not _equivalent(
                "inventory_quantity", existing, variant["inventory_quantity"]
            ):
                changed.add(path)
    return changed


def _normalize_tags(value: Any) -> str | list[str] | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return None


def _normalize_metafields_payload(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        namespace = str(item.get("namespace") or "").strip()
        key = str(item.get("key") or "").strip()
        value = item.get("value")
        if not namespace or not key or value is None:
            continue
        normalized_item: dict[str, Any] = {
            "namespace": namespace,
            "key": key,
            "value": str(value),
        }
        value_type = item.get("type")
        if isinstance(value_type, str) and value_type.strip():
            normalized_item["type"] = value_type.strip()
        normalized.append(normalized_item)
    return normalized


def _normalize_variant_operations(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    create_options_raw = raw.get("create_options")
    create_options: list[dict[str, Any]] = []
    if isinstance(create_options_raw, list):
        for option in create_options_raw:
            if not isinstance(option, dict):
                continue
            name = str(option.get("name") or "").strip()
            values_raw = option.get("values")
            values = (
                [str(item).strip() for item in values_raw if str(item).strip()]
                if isinstance(values_raw, list)
                else []
            )
            if name and values:
                create_options.append({"name": name, "values": values})

    create_variants_raw = raw.get("create_variants")
    create_variants: list[dict[str, Any]] = []
    if isinstance(create_variants_raw, list):
        for variant in create_variants_raw:
            if not isinstance(variant, dict):
                continue
            option_values_raw = variant.get("option_values")
            option_values = (
                [item for item in option_values_raw if isinstance(item, dict)]
                if isinstance(option_values_raw, list)
                else []
            )
            if not option_values:
                continue
            payload: dict[str, Any] = {"option_values": option_values}
            sku = str(variant.get("sku") or "").strip()
            if sku:
                payload["sku"] = sku
            price = variant.get("price")
            if price not in (None, ""):
                payload["price"] = str(price)
            inventory_quantity = variant.get("inventory_quantity")
            if isinstance(inventory_quantity, int):
                payload["inventory_quantity"] = inventory_quantity
            create_variants.append(payload)

    defaults = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    return {
        "create_options": create_options,
        "create_variants": create_variants,
        "defaults": defaults,
    }


def _merge_metafields(existing_raw: Any, incoming_raw: Any) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in _normalize_metafields_payload(existing_raw):
        merged[(item["namespace"], item["key"])] = item
    for item in _normalize_metafields_payload(incoming_raw):
        merged[(item["namespace"], item["key"])] = item
    return list(merged.values())


def _apply_patch_payload(
    *,
    product: dict[str, Any],
    patch_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = dict(product)
    for key in SUPPORTED_SUGGESTION_FIELDS:
        if key not in patch_payload:
            continue
        if key == "metafields":
            merged = _merge_metafields(
                updated.get("metafields"), patch_payload.get("metafields")
            )
            if merged:
                updated["metafields"] = merged
            continue
        if key == "tags":
            tags = _normalize_tags(patch_payload.get("tags"))
            if tags is not None:
                updated["tags"] = tags
            continue
        value = patch_payload.get(key)
        if value is None:
            continue
        updated[key] = value
    variant_operations = _normalize_variant_operations(
        patch_payload.get("variant_operations")
    )
    return updated, variant_operations


def apply_suggestions_to_products(
    *,
    products: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    enhanced_products = [dict(item) for item in products]
    variant_operations_by_index: dict[int, list[dict[str, Any]]] = {}
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        patch_payload = suggestion.get("patch_payload")
        if not isinstance(patch_payload, dict) or not patch_payload:
            continue
        try:
            product_index = int(suggestion.get("product_index"))
        except (TypeError, ValueError):
            continue
        if product_index < 0 or product_index >= len(enhanced_products):
            continue
        current = enhanced_products[product_index]
        updated, variant_operations = _apply_patch_payload(
            product=current,
            patch_payload=patch_payload,
        )
        origin = suggestion.get("value_origin")
        if origin in {"ai_assisted", "derived"}:
            raw_origins = updated.get("value_origins") or updated.get("_value_origins")
            origins = dict(raw_origins) if isinstance(raw_origins, dict) else {}
            changed_paths = _changed_patch_origin_paths(
                product=current,
                patch_payload=patch_payload,
                variant_operations=variant_operations,
            )
            if origins or changed_paths:
                updated["value_origins"] = origins
            for path in changed_paths:
                origins[path] = origin
        enhanced_products[product_index] = updated
        if variant_operations.get("create_options") or variant_operations.get(
            "create_variants"
        ):
            variant_operations_by_index.setdefault(product_index, []).append(
                variant_operations
            )
    return enhanced_products, variant_operations_by_index
