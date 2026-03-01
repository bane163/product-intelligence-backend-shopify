from __future__ import annotations

import json
from typing import Any, Dict, List

_PRODUCT_FIELD_MAPPING = {
    "title": "title",
    "handle": "handle",
    "body_html": "descriptionHtml",
    "vendor": "vendor",
    "product_type": "productType",
    "status": "status",
}
_PRODUCT_MUTATION_FIELD_MAPPING = {"id": "id", **_PRODUCT_FIELD_MAPPING}
_CLEARABLE_STRING_FIELDS = {"vendor", "body_html", "product_type"}


def normalize_tags(tags: Any, *, allow_empty: bool = False) -> list[str] | None:
    if tags is None:
        return None
    if isinstance(tags, list):
        normalized = [str(tag).strip() for tag in tags if str(tag).strip()]
        if normalized:
            return normalized
        return [] if allow_empty else None
    if isinstance(tags, str):
        normalized = [part.strip() for part in tags.split(",") if part.strip()]
        if normalized:
            return normalized
        return [] if allow_empty else None
    return None


def _build_seo_payload(
    product: Dict[str, Any], *, include_empty_when_explicit: bool
) -> dict[str, str] | None:
    seo_title = product.get("seo_title")
    seo_description = product.get("seo_description")
    include_seo = (
        (
            "seo_title" in product
            or "seo_description" in product
            or seo_title not in (None, "")
            or seo_description not in (None, "")
        )
        if include_empty_when_explicit
        else bool(seo_title or seo_description)
    )
    if not include_seo:
        return None
    return {
        "title": str(seo_title or ""),
        "description": str(seo_description or ""),
    }


def _apply_field_mapping(
    product: Dict[str, Any],
    mapping: Dict[str, str],
    *,
    allow_empty_for: set[str] | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    allow_empty = allow_empty_for or set()
    for source_key, target_key in mapping.items():
        if source_key not in product:
            continue
        value = product.get(source_key)
        if value is None:
            continue
        if value == "" and source_key not in allow_empty:
            continue
        payload[target_key] = value
    return payload


def build_product_payload(
    product: Dict[str, Any], *, include_id: bool
) -> Dict[str, Any]:
    mapping = _PRODUCT_MUTATION_FIELD_MAPPING if include_id else _PRODUCT_FIELD_MAPPING
    payload = _apply_field_mapping(
        product,
        mapping,
        allow_empty_for=_CLEARABLE_STRING_FIELDS,
    )
    tags = normalize_tags(
        product.get("tags"),
        allow_empty="tags" in product,
    )
    if tags is not None:
        payload["tags"] = tags
    if "productType" not in payload and "product_category" in product:
        fallback_type = product.get("product_category")
        if fallback_type is not None:
            payload["productType"] = fallback_type
    seo_payload = _build_seo_payload(product, include_empty_when_explicit=True)
    if seo_payload is not None:
        payload["seo"] = seo_payload
    return payload


def build_product_set_input(product: Dict[str, Any]) -> Dict[str, Any]:
    payload = _apply_field_mapping(product, _PRODUCT_FIELD_MAPPING)
    tags = normalize_tags(
        product.get("tags"),
        allow_empty="tags" in product,
    )
    if tags is not None:
        payload["tags"] = tags
    seo_payload = _build_seo_payload(product, include_empty_when_explicit=False)
    if seo_payload is not None:
        payload["seo"] = seo_payload
    return payload


def build_product_set_identifier(product: Dict[str, Any]) -> Dict[str, str] | None:
    gid = product.get("id") or product.get("shopify_gid")
    if isinstance(gid, str) and gid.strip():
        return {"id": gid.strip()}
    handle = product.get("handle")
    if isinstance(handle, str) and handle.strip():
        return {"handle": handle.strip()}
    return None


def build_product_set_jsonl(products: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for product in products:
        line: Dict[str, Any] = {"input": build_product_set_input(product)}
        identifier = build_product_set_identifier(product)
        if identifier:
            line["identifier"] = identifier
        lines.append(json.dumps(line, ensure_ascii=False))
    return "\n".join(lines)


def normalize_product_options(options: Any) -> List[Dict[str, Any]]:
    if not isinstance(options, list):
        return []
    normalized_options: List[Dict[str, Any]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        name = str(option.get("name") or "").strip()
        raw_values = option.get("values")
        values = (
            [str(item).strip() for item in raw_values if str(item).strip()]
            if isinstance(raw_values, list)
            else []
        )
        if not name or not values:
            continue
        normalized_options.append(
            {"name": name, "values": [{"name": value} for value in values]}
        )
    return normalized_options


def normalize_variant_inputs(variants: Any) -> List[Dict[str, Any]]:
    if not isinstance(variants, list):
        return []
    normalized_variants: List[Dict[str, Any]] = []
    for item in variants:
        if not isinstance(item, dict):
            continue
        option_values = item.get("option_values")
        if not isinstance(option_values, list):
            option_values = []
        normalized_option_values: List[Dict[str, str]] = []
        for option in option_values:
            if not isinstance(option, dict):
                continue
            option_name = str(
                option.get("option_name") or option.get("optionName") or ""
            ).strip()
            name = str(option.get("name") or option.get("value") or "").strip()
            if option_name and name:
                normalized_option_values.append(
                    {"optionName": option_name, "name": name}
                )
        if not normalized_option_values:
            continue
        payload: Dict[str, Any] = {"optionValues": normalized_option_values}
        sku = str(item.get("sku") or "").strip()
        if sku:
            payload["sku"] = sku
        price = item.get("price")
        if price not in (None, ""):
            payload["price"] = str(price)
        inventory_quantity = item.get("inventory_quantity")
        if isinstance(inventory_quantity, int):
            payload["inventoryQuantities"] = [
                {"availableQuantity": inventory_quantity}
            ]
        normalized_variants.append(payload)
    return normalized_variants


def normalize_variant_ids(variant_ids: Any) -> List[str]:
    if not isinstance(variant_ids, list):
        return []
    return [str(item).strip() for item in variant_ids if str(item).strip()]
