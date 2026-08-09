"""Server-owned origin and evidence rules for canonical import fields."""

from __future__ import annotations

import re
from typing import Any

from application.domain.review_value_paths import canonical_value_path, iter_review_values


NON_SOURCE_ORIGINS = {"ai_assisted", "derived", "defaulted"}

# These values affect commerce, fulfillment, or physical inventory and may not
# be invented by an extractor. Merchant edits happen later in the review flow.
PROTECTED_FIELDS = {
    "price",
    "compareatprice",
    "inventoryitem.cost",
    "sku",
    "barcode",
    "inventory_quantity",
    "inventorypolicy",
    "inventoryitem.tracked",
    "inventory_tracker",
    "inventory",
    "taxable",
    "taxcode",
    "variant_tax_code",
    "inventoryitem.requiresshipping",
    "fulfillment_service",
    "fulfillmentservice",
    "inventoryitem.weight.value",
    "inventoryitem.weight.unit",
    "grams",
    "pricing",
    "internationalprices",
    "price_international",
    "compare_at_price_international",
}

PROTECTED_VARIANT_ALIASES = {
    "price": ("price",),
    "compareAtPrice": ("compareAtPrice", "compare_at_price", "compare_at"),
    "sku": ("sku",), "barcode": ("barcode",), "taxable": ("taxable",),
    "taxCode": ("taxCode", "tax_code"),
    "inventoryPolicy": ("inventoryPolicy", "inventory_policy"),
    "inventoryItem.cost": ("cost", "unit_cost", "cost_per_item"),
    "inventoryItem.tracked": ("tracked", "inventory_tracked", "track_inventory"),
    "inventoryItem.requiresShipping": ("requiresShipping", "requires_shipping"),
    "inventoryItem.weight.value": ("weight",),
    "inventoryItem.weight.unit": ("weightUnit", "weight_unit", "variant_weight_unit"),
    "inventory_quantity": ("inventory_quantity", "inventoryQuantity", "inventoryQuantities"),
    "fulfillment_service": ("fulfillment_service", "fulfillmentService"),
    "inventory_tracker": ("inventory_tracker", "inventoryTracker"),
    "grams": ("grams",), "variant_tax_code": ("variant_tax_code",),
    "price_international": ("price_international", "international_price"),
    "compare_at_price_international": (
        "compare_at_price_international", "international_compare_at_price",
    ),
}


def is_protected_field(value_path: Any, field: Any = None) -> bool:
    path = canonical_value_path(value_path)
    normalized_field = canonical_value_path(field)
    leaf = path.rsplit("].", 1)[-1] if "]." in path else path
    return normalized_field in PROTECTED_FIELDS or leaf in PROTECTED_FIELDS


def _slugify(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().casefold()).strip("-")


def apply_server_owned_origins(product: dict[str, Any]) -> None:
    """Own deterministic handle and Shopify single-variant scaffolding."""
    origins = product.get("value_origins")
    if not isinstance(origins, dict):
        origins = {}
    refs = product.get("source_refs")
    refs = [ref for ref in refs if isinstance(ref, dict)] if isinstance(refs, list) else []

    title = str(product.get("title") or "").strip()
    canonical_handle = _slugify(title)
    existing_handle = str(product.get("handle") or "").strip()
    if canonical_handle and (not existing_handle or existing_handle == canonical_handle):
        if not existing_handle:
            product["handle"] = canonical_handle
        if str(product.get("handle") or "").strip() == canonical_handle:
            origins["handle"] = "derived"

    options = product.get("options")
    variants = product.get("variants")
    variant_list = variants.get("nodes") if isinstance(variants, dict) else variants
    if not isinstance(variant_list, list):
        variant_list = []
    if (not isinstance(options, list) or not options) and len(variant_list) == 1:
        product["options"] = [{"name": "Title", "values": ["Default Title"]}]
        origins["option[title].values"] = "defaulted"
        if len(variant_list) == 1 and isinstance(variant_list[0], dict):
            variant_list[0].setdefault("option1", "Default Title")
    elif (
        isinstance(options, list)
        and len(options) == 1
        and isinstance(options[0], dict)
        and str(options[0].get("name") or "").casefold() == "title"
        and options[0].get("values") == ["Default Title"]
    ):
        origins["option[title].values"] = "defaulted"

    policy_options = product.get("options")
    policy_options = policy_options if isinstance(policy_options, list) else []
    default_title_option = any(
        isinstance(option, dict)
        and str(option.get("name") or "").strip().casefold() == "title"
        and (
            option.get("values") == ["Default Title"]
            or option.get("optionValues") == [{"name": "Default Title"}]
        )
        for option in policy_options
    )
    if default_title_option:
        for item in iter_review_values(product):
            if (
                item.scope == "variant"
                and item.field == "title"
                and str(item.value).strip().casefold() == "default title"
            ):
                origins[item.value_path] = "defaulted"

    non_source_paths = {
        canonical_value_path(path)
        for path, origin in origins.items()
        if origin in {"derived", "defaulted"}
    }
    product["source_refs"] = [
        ref for ref in refs
        if canonical_value_path(ref.get("value_path") or ref.get("field")) not in non_source_paths
    ]
    product["value_origins"] = origins


def remove_unverified_protected_values(
    product: dict[str, Any], valid_paths: set[str]
) -> set[str]:
    """Blank protected extracted values unless a trusted adapter verified them."""
    items = list(iter_review_values(product))
    unsupported = {
        canonical_value_path(item.value_path)
        for item in items
        if is_protected_field(item.value_path, item.field)
        and canonical_value_path(item.value_path) not in valid_paths
    }
    removed: set[str] = set()
    for item in items:
        path = canonical_value_path(item.value_path)
        if path not in unsupported or item.scope != "product":
            continue
        for key in tuple(product):
            if canonical_value_path(key) == canonical_value_path(item.field):
                product.pop(key, None)
                removed.add(path)

    variants = product.get("variants")
    variants = variants.get("nodes") if isinstance(variants, dict) else variants
    variants_by_prefix: dict[str, dict[str, Any]] = {}
    for variant in variants if isinstance(variants, list) else []:
        if not isinstance(variant, dict):
            continue
        probe = {**product, "variants": [variant]}
        for candidate in iter_review_values(probe):
            if candidate.scope == "variant":
                variants_by_prefix[
                    canonical_value_path(candidate.value_path).rsplit(".", 1)[0]
                ] = variant
    for item in items:
        path = canonical_value_path(item.value_path)
        if path not in unsupported or item.scope != "variant":
            continue
        aliases = PROTECTED_VARIANT_ALIASES.get(item.field, ())
        prefix = path.rsplit(".", 1)[0]
        variant = variants_by_prefix.get(prefix)
        if variant is not None:
            inventory = variant.get("inventoryItem") or variant.get("inventory_item")
            for alias in aliases:
                variant.pop(alias, None)
                if isinstance(inventory, dict):
                    inventory.pop(alias, None)
            removed.add(path)

    refs = product.get("source_refs")
    if isinstance(refs, list):
        product["source_refs"] = [
            ref for ref in refs
            if not isinstance(ref, dict)
            or canonical_value_path(ref.get("value_path") or ref.get("field")) not in removed
        ]
    for origin_key in ("value_origins", "_value_origins"):
        origins = product.get(origin_key)
        if isinstance(origins, dict):
            product[origin_key] = {
                key: value for key, value in origins.items()
                if canonical_value_path(key) not in removed
            }
    return removed
