"""Canonical review-value paths and conservative source-value comparison."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Iterable

from application.domain.shopify_product_normalization import normalize_core_product


@dataclass(frozen=True)
class ReviewValue:
    value_path: str
    field: str
    value: Any
    scope: str = "product"
    subject: str = "Product"


def normalized_path_part(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().casefold()).strip("-") or "value"


def canonical_value_path(value: Any) -> str:
    """Normalize equivalent persisted/model path spellings to the review contract."""
    path = str(value or "").strip()
    structured = re.fullmatch(r"([^\[\]]+)\[(.*)\]\.(.+)", path)
    if structured:
        scope, subject, field = structured.groups()
        scope = {
            "options": "option", "variants": "variant", "metafields": "metafield",
            "inventories": "inventory",
        }.get(scope.strip().casefold(), scope.strip().casefold())
        return f"{scope}[{normalized_path_part(subject)}].{field.strip()}".casefold()
    aliases = {
        "body_html": "descriptionHtml", "bodyhtml": "descriptionHtml",
        "description": "descriptionHtml", "product_type": "productType",
        "product_category": "productType", "seo_title": "seo.title",
        "seo_description": "seo.description",
    }
    return aliases.get(path.casefold(), path).casefold()


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = re.sub(r"\s+", " ", str(value).strip()).casefold()
    return result or None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    raw = str(value).strip().replace(",", "")
    raw = re.sub(r"^[\$£€]", "", raw).strip()
    try:
        return Decimal(raw).normalize() if raw else None
    except InvalidOperation:
        return None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in {"true", "yes", "1"}:
            return True
        if token in {"false", "no", "0"}:
            return False
    return None


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list, bool, int, float)) or value is None:
        return value
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value.strip())
    except (ValueError, TypeError):
        return value


def _list(value: Any) -> list[Any] | None:
    parsed = _json(value)
    if isinstance(parsed, list):
        return parsed
    if not isinstance(value, str):
        return None
    # Spreadsheet extraction and Shopify CSV commonly use these delimiters.
    delimiter = next((item for item in (";", "|", "\n", ",") if item in value), None)
    if delimiter is None:
        return [value.strip()] if value.strip() else None
    return [item.strip() for item in value.split(delimiter) if item.strip()]


def is_numeric_field(field: str) -> bool:
    return field in {
        "price", "compareAtPrice", "inventoryItem.cost",
        "inventoryItem.weight.value", "inventory_quantity",
    }


def values_equal(source: Any, expected: Any, *, numeric_strings: bool = False) -> bool:
    """Compare source data by expected type without truthiness shortcuts."""
    if isinstance(expected, bool):
        return _boolean(source) is expected
    if isinstance(expected, (int, float, Decimal)) and not isinstance(expected, bool):
        left, right = _decimal(source), _decimal(expected)
        return left is not None and right is not None and left == right
    if isinstance(expected, dict):
        parsed = _json(source)
        return isinstance(parsed, dict) and _structural_equal(parsed, expected)
    if isinstance(expected, list):
        parsed = _list(source)
        return parsed is not None and _structural_equal(parsed, expected)
    # Money often arrives as a string; accept Decimal equality before exact text.
    if numeric_strings and isinstance(expected, str):
        left, right = _decimal(source), _decimal(expected)
        if left is not None and right is not None:
            return left == right
    return _text(source) is not None and _text(source) == _text(expected)


def _structural_equal(left: Any, right: Any) -> bool:
    if isinstance(right, bool):
        return _boolean(left) is right
    if isinstance(right, (int, float, Decimal)) and not isinstance(right, bool):
        a, b = _decimal(left), _decimal(right)
        return a is not None and b is not None and a == b
    if isinstance(right, dict):
        return isinstance(left, dict) and set(left) == set(right) and all(
            _structural_equal(left[key], right[key]) for key in right
        )
    if isinstance(right, list):
        return isinstance(left, list) and len(left) == len(right) and all(
            _structural_equal(a, b) for a, b in zip(left, right)
        )
    return _text(left) == _text(right)


def _options(product: dict[str, Any]) -> list[dict[str, Any]]:
    raw = product.get("productOptions") if isinstance(product.get("productOptions"), list) else product.get("options")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _option_values(option: dict[str, Any]) -> list[Any]:
    raw = option.get("values") if isinstance(option.get("values"), list) else option.get("optionValues")
    if not isinstance(raw, list):
        return []
    return [item.get("name") if isinstance(item, dict) else item for item in raw]


def _variant_pairs(variant: dict[str, Any], option_names: list[str]) -> list[tuple[str, str]]:
    raw = variant.get("selectedOptions") or variant.get("selected_options") or variant.get("optionValues") or variant.get("option_values")
    pairs: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            name = item.get("optionName") or item.get("option_name")
            value = item.get("name")
            if "value" in item:
                name, value = item.get("name") or name, item.get("value")
            if not name and index < len(option_names):
                name = option_names[index]
            if name is not None and value is not None:
                pairs.append((str(name).strip(), str(value).strip()))
    if not pairs:
        for index, name in enumerate(option_names[:3], 1):
            if variant.get(f"option{index}") not in (None, ""):
                pairs.append((name, str(variant[f"option{index}"]).strip()))
    return pairs


def _variant_subject(variant: dict[str, Any], option_names: list[str]) -> str:
    pairs = _variant_pairs(variant, option_names)
    if pairs:
        return " / ".join(f"{name}: {value}" for name, value in pairs)
    return str(variant.get("title") or variant.get("id") or variant.get("sku") or "Variant").strip()


def iter_review_values(product: dict[str, Any]) -> Iterable[ReviewValue]:
    """Yield every directly extracted value that can become a review row."""
    core = normalize_core_product(product)
    for field in ("title", "handle", "descriptionHtml", "vendor", "productType", "status", "tags"):
        if field in core:
            yield ReviewValue(field, field, core[field])
    seo = core.get("seo")
    if isinstance(seo, dict):
        for field in ("title", "description"):
            if field in seo:
                yield ReviewValue(f"seo.{field}", f"seo.{field}", seo[field])

    # Preserve directly extracted attributes that currently become deferred
    # review rows instead of mutation input. Evidence is still useful even
    # when Shopify cannot apply the value yet.
    for field in (
        "media", "category", "inventory", "taxonomy", "price", "pricing",
        "publications", "compareAtPrice", "internationalPrices",
        "fulfillmentService", "googleShopping", "published", "gift_card",
        "image_src", "image_position", "image_alt_text",
        "google_shopping_category", "google_shopping_gender",
        "google_shopping_age_group", "google_shopping_mpn",
        "google_adwords_grouping", "google_adwords_labels",
        "google_shopping_condition", "google_shopping_custom_product",
        "google_custom_label_0", "google_custom_label_1",
        "google_custom_label_2", "google_custom_label_3",
        "google_custom_label_4",
    ):
        if field in product and product[field] not in (None, "", [], {}):
            yield ReviewValue(field, field, product[field])

    options = _options(product)
    option_names = [str(option.get("name") or "").strip() for option in options if option.get("name")]
    for option in options:
        name = str(option.get("name") or "").strip()
        values = _option_values(option)
        if name and values:
            yield ReviewValue(f"option[{normalized_path_part(name)}].values", "values", values, "option", name)

    variants = product.get("variants")
    if isinstance(variants, dict):
        variants = variants.get("nodes")
    for variant in variants if isinstance(variants, list) else []:
        if not isinstance(variant, dict):
            continue
        subject = _variant_subject(variant, option_names)
        prefix = f"variant[{normalized_path_part(subject)}]"
        inventory = variant.get("inventoryItem") or variant.get("inventory_item")
        inventory = inventory if isinstance(inventory, dict) else {}
        fields: dict[str, Any] = {}
        for target, aliases in {
            "price": ("price",),
            "compareAtPrice": ("compareAtPrice", "compare_at_price", "compare_at"),
            "sku": ("sku",),
            "barcode": ("barcode",),
            "taxable": ("taxable",),
            "taxCode": ("taxCode", "tax_code"),
            "inventoryPolicy": ("inventoryPolicy", "inventory_policy"),
        }.items():
            alias = next((name for name in aliases if name in variant and variant[name] is not None), None)
            if alias is not None:
                fields[target] = variant[alias]
        for target, aliases in {
            "inventoryItem.cost": ("cost", "unit_cost", "cost_per_item"),
            "inventoryItem.tracked": ("tracked", "inventory_tracked", "track_inventory"),
            "inventoryItem.requiresShipping": ("requiresShipping", "requires_shipping"),
        }.items():
            for alias in aliases:
                value = variant.get(alias) if alias in variant else inventory.get(alias)
                if value is not None:
                    fields[target] = value
                    break
        for target, aliases in {
            "inventoryItem.weight.value": ("weight",),
            "inventoryItem.weight.unit": ("weightUnit", "weight_unit", "variant_weight_unit"),
        }.items():
            alias = next((name for name in aliases if name in variant and variant[name] is not None), None)
            if alias is not None:
                fields[target] = variant[alias]
        for target, aliases in {
            "inventory_quantity": (
                "inventory_quantity", "inventoryQuantity", "inventoryQuantities",
            ),
            "fulfillment_service": (
                "fulfillment_service", "fulfillmentService",
            ),
            "inventory_tracker": (
                "inventory_tracker", "inventoryTracker",
            ),
            "grams": ("grams",),
            "variant_image": ("variant_image", "image"),
            "variant_tax_code": ("variant_tax_code",),
            "price_international": ("price_international", "international_price"),
            "compare_at_price_international": (
                "compare_at_price_international", "international_compare_at_price",
            ),
            "status": ("status",),
            "title": ("title",),
        }.items():
            alias = next(
                (name for name in aliases if name in variant and variant[name] is not None),
                None,
            )
            if alias is not None:
                fields[target] = variant[alias]
        for field, value in fields.items():
            yield ReviewValue(f"{prefix}.{field}", field, value, "variant", subject)

    metafields = product.get("metafields")
    if isinstance(metafields, dict):
        metafields = metafields.get("nodes")
    for metafield in metafields if isinstance(metafields, list) else []:
        if not isinstance(metafield, dict) or not metafield.get("namespace") or not metafield.get("key"):
            continue
        subject = f"{metafield['namespace']}.{metafield['key']}"
        for field in ("value", "type"):
            if field in metafield and metafield[field] is not None:
                value = metafield[field]
                if field == "value" and "json" in str(metafield.get("type") or "").casefold():
                    parsed = _json(value)
                    if isinstance(parsed, (dict, list)):
                        value = parsed
                yield ReviewValue(
                    f"metafield[{normalized_path_part(subject)}].{field}", field,
                    value, "metafield", subject,
                )
