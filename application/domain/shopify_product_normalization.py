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

_CORE_PRODUCT_ALIASES = {
    "title": ("title",),
    "handle": ("handle",),
    "descriptionHtml": ("descriptionHtml", "body_html", "bodyHtml", "description"),
    "vendor": ("vendor",),
    "productType": ("productType", "product_type", "product_category"),
    "status": ("status",),
    "tags": ("tags",),
}


def canonicalize_single_option_variant_titles(product: Dict[str, Any]) -> bool:
    """Copy an exact single-option title alias into its authoritative column.

    Extractors occasionally place a variant's sole option value in ``title``.
    Only the unambiguous one-dimension case is repaired: the title must match
    one declared value after trimming and case-folding. The declared spelling
    wins, and multi-option titles are deliberately never parsed or guessed.
    """
    raw_options = (
        product.get("productOptions")
        if isinstance(product.get("productOptions"), list)
        else product.get("options")
    )
    if not isinstance(raw_options, list) or len(raw_options) != 1:
        return False
    option = raw_options[0]
    if not isinstance(option, dict):
        return False
    raw_values = option.get("values")
    if not isinstance(raw_values, list):
        raw_values = option.get("optionValues")
    if not isinstance(raw_values, list):
        return False
    declared: dict[str, str] = {}
    ambiguous: set[str] = set()
    for entry in raw_values:
        value = entry.get("name") if isinstance(entry, dict) else entry
        if not isinstance(value, str) or not value.strip():
            continue
        canonical = value.strip()
        folded = canonical.casefold()
        if folded in declared and declared[folded] != canonical:
            ambiguous.add(folded)
        else:
            declared[folded] = canonical
    for folded in ambiguous:
        declared.pop(folded, None)

    raw_variants = product.get("variants")
    variants = (
        raw_variants.get("nodes")
        if isinstance(raw_variants, dict)
        else raw_variants
    )
    if not isinstance(variants, list):
        return False
    changed = False
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        option1 = variant.get("option1")
        if isinstance(option1, str) and option1.strip():
            continue
        title = variant.get("title")
        if not isinstance(title, str):
            continue
        canonical = declared.get(title.strip().casefold())
        if canonical is not None:
            variant["option1"] = canonical
            changed = True
    return changed


def normalize_core_product(product: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize extracted and Shopify product aliases to ProductSetInput keys."""
    result: Dict[str, Any] = {}
    for target, aliases in _CORE_PRODUCT_ALIASES.items():
        source = next((key for key in aliases if key in product), None)
        if source is None:
            continue
        value = product.get(source)
        if target == "tags":
            value = normalize_tags(value, allow_empty=True)
        elif isinstance(value, str):
            value = value.strip()
            if target == "status":
                value = value.upper()
        if value not in (None, "", [], {}):
            result[target] = value

    raw_seo = product.get("seo") if isinstance(product.get("seo"), dict) else {}
    seo_title = next(
        (
            product.get(key)
            for key in ("seo_title", "seoTitle", "meta_title")
            if key in product
        ),
        raw_seo.get("title"),
    )
    seo_description = next(
        (
            product.get(key)
            for key in ("seo_description", "seoDescription", "meta_description")
            if key in product
        ),
        raw_seo.get("description"),
    )
    seo = {
        key: value.strip() if isinstance(value, str) else value
        for key, value in {"title": seo_title, "description": seo_description}.items()
        if value not in (None, "")
    }
    if seo:
        result["seo"] = seo
    return result


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
    payload: dict[str, str] = {}
    for source, target in (
        ("seo_title", "title"),
        ("seo_description", "description"),
    ):
        if source not in product:
            continue
        value = product.get(source)
        if value not in (None, "") or include_empty_when_explicit:
            payload[target] = str(value or "")
    return payload or None


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
        if source_key == "status" and isinstance(value, str):
            value = value.strip().upper()
            if not value:
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
    payload = normalize_core_product(product)
    raw_options = product.get("productOptions") or product.get("options")
    raw_variants = product.get("variants")
    if isinstance(raw_options, list):
        payload["productOptions"] = normalize_product_options(raw_options)
    if isinstance(raw_variants, list):
        payload["variants"] = normalize_variant_inputs(raw_variants)
    raw_files = product.get("files")
    if isinstance(raw_files, list) and raw_files:
        normalized_files = [
            {
                key: value
                for key, value in item.items()
                if key in {"id", "contentType", "originalSource", "alt", "filename"}
                and value not in (None, "")
            }
            for item in raw_files
            if isinstance(item, dict)
            and (item.get("id") or item.get("originalSource"))
        ]
        if normalized_files:
            payload["files"] = normalized_files
    return payload


def build_product_set_identifier(product: Dict[str, Any]) -> Dict[str, str] | None:
    gid = product.get("id") or product.get("shopify_gid")
    if isinstance(gid, str) and gid.strip():
        return {"id": gid.strip()}
    # Handles are discovery keys, never mutation authority. Only a Shopify GID
    # resolved by the server may identify an existing product.
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
        if not isinstance(raw_values, list):
            raw_values = option.get("optionValues")
        values = []
        if isinstance(raw_values, list):
            for item in raw_values:
                value = item.get("name") if isinstance(item, dict) else item
                value = str(value or "").strip()
                if value:
                    values.append(value)
        if not name or not values:
            continue
        payload: Dict[str, Any] = {
            "name": name,
            "values": [{"name": value} for value in values],
        }
        if isinstance(option.get("id"), str) and option["id"].strip():
            payload["id"] = option["id"].strip()
        if isinstance(option.get("position"), int):
            payload["position"] = option["position"]
        normalized_options.append(payload)
    return normalized_options


def normalize_variant_inputs(
    variants: Any, strip_sku: bool = False
) -> List[Dict[str, Any]]:
    if not isinstance(variants, list):
        return []
    normalized_variants: List[Dict[str, Any]] = []
    for item in variants:
        if not isinstance(item, dict):
            continue
        option_values = item.get("option_values")
        if not isinstance(option_values, list):
            option_values = item.get("optionValues")
        if not isinstance(option_values, list):
            option_values = item.get("selectedOptions")
        if not isinstance(option_values, list):
            option_values = []
        normalized_option_values: List[Dict[str, str]] = []
        for option in option_values:
            if not isinstance(option, dict):
                continue
            option_name = str(
                option.get("option_name")
                or option.get("optionName")
                or option.get("name")
                or ""
            ).strip()
            name = str(option.get("value") or option.get("name") or "").strip()
            if option_name and name:
                normalized_option_values.append(
                    {"optionName": option_name, "name": name}
                )
        if not normalized_option_values:
            continue
        payload: Dict[str, Any] = {"optionValues": normalized_option_values}
        if isinstance(item.get("id"), str) and item["id"].strip():
            payload["id"] = item["id"].strip()
        for field in (
            "sku",
            "price",
            "compareAtPrice",
            "barcode",
            "taxable",
            "taxCode",
            "inventoryPolicy",
        ):
            if (
                field not in item
                or item[field] is None
                or (field == "sku" and strip_sku)
            ):
                continue
            value = item[field]
            if field in {"price", "compareAtPrice"} and value != "":
                value = str(value)
            payload[field] = value
        inventory_item = item.get("inventoryItem")
        if isinstance(inventory_item, dict) and inventory_item:
            allowed_inventory = {
                key: value
                for key, value in inventory_item.items()
                if key in {"sku", "cost", "tracked", "requiresShipping", "measurement"}
                and value is not None
            }
            if allowed_inventory:
                payload["inventoryItem"] = allowed_inventory
        normalized_variants.append(payload)
    return normalized_variants


def normalize_variant_ids(variant_ids: Any) -> List[str]:
    if not isinstance(variant_ids, list):
        return []
    return [str(item).strip() for item in variant_ids if str(item).strip()]
