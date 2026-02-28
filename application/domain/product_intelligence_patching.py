from __future__ import annotations

from typing import Any

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
        updated, variant_operations = _apply_patch_payload(
            product=enhanced_products[product_index],
            patch_payload=patch_payload,
        )
        enhanced_products[product_index] = updated
        if variant_operations.get("create_options") or variant_operations.get(
            "create_variants"
        ):
            variant_operations_by_index.setdefault(product_index, []).append(
                variant_operations
            )
    return enhanced_products, variant_operations_by_index

