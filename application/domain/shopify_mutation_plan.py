"""Pure validation, merge, and diff helpers for reviewed Shopify mutations."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from application.domain.shopify_product_normalization import normalize_core_product
from application.domain.review_value_paths import canonical_value_path, normalized_path_part

SUPPORTED_PRODUCT_FIELDS = {
    "title",
    "handle",
    "descriptionHtml",
    "vendor",
    "productType",
    "status",
    "tags",
    "seo",
}
STRUCTURED_FIELDS = {"options", "variants", "metafields"}
DEFERRED_FIELDS = {
    "media": "UNAPPLIED_MEDIA",
    "category": "UNAPPLIED_TAXONOMY_CATEGORY",
    "inventory": "UNAPPLIED_INVENTORY",
    "taxonomy": "UNAPPLIED_TAXONOMY",
    "price": "UNAPPLIED_PRICE",
    "pricing": "UNAPPLIED_PRICING",
    "publications": "UNAPPLIED_PUBLICATION",
    "compareAtPrice": "UNAPPLIED_COMPARE_AT_PRICE",
    "internationalPrices": "TODO_MARKETS_PRICES",
    "fulfillmentService": "TODO_FULFILLMENT_LOCATION",
    "googleShopping": "TODO_GOOGLE_SHOPPING",
    "published": "UNAPPLIED_PUBLICATION",
    "gift_card": "UNAPPLIED_PRODUCT_ATTRIBUTE",
    "image_src": "UNAPPLIED_MEDIA",
    "image_position": "UNAPPLIED_MEDIA",
    "image_alt_text": "UNAPPLIED_MEDIA",
    "google_shopping_category": "TODO_GOOGLE_SHOPPING",
    "google_shopping_gender": "TODO_GOOGLE_SHOPPING",
    "google_shopping_age_group": "TODO_GOOGLE_SHOPPING",
    "google_shopping_mpn": "TODO_GOOGLE_SHOPPING",
    "google_adwords_grouping": "TODO_GOOGLE_SHOPPING",
    "google_adwords_labels": "TODO_GOOGLE_SHOPPING",
    "google_shopping_condition": "TODO_GOOGLE_SHOPPING",
    "google_shopping_custom_product": "TODO_GOOGLE_SHOPPING",
    "google_custom_label_0": "TODO_GOOGLE_SHOPPING",
    "google_custom_label_1": "TODO_GOOGLE_SHOPPING",
    "google_custom_label_2": "TODO_GOOGLE_SHOPPING",
    "google_custom_label_3": "TODO_GOOGLE_SHOPPING",
    "google_custom_label_4": "TODO_GOOGLE_SHOPPING",
}
VALID_STATUSES = {"ACTIVE", "ARCHIVED", "DRAFT", "UNLISTED"}
VALUE_ORIGINS = {"source", "ai_assisted", "merchant_edited", "derived", "unknown"}
HANDLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_OPTIONS = 3
MAX_VARIANTS = 2048

_VARIANT_ALIASES = {
    "sku": ("sku",),
    "price": ("price",),
    "compareAtPrice": ("compareAtPrice", "compare_at_price", "compare_at"),
    "barcode": ("barcode",),
    "taxable": ("taxable",),
    "taxCode": ("taxCode", "tax_code"),
    "inventoryPolicy": ("inventoryPolicy", "inventory_policy"),
}
_INVENTORY_ALIASES = {
    "cost": ("cost", "unit_cost", "cost_per_item"),
    "tracked": ("tracked", "inventory_tracked", "track_inventory"),
    "requiresShipping": ("requiresShipping", "requires_shipping"),
}
_VARIANT_SHAPE_KEYS = {
    "id",
    "title",
    "selectedOptions",
    "selected_options",
    "optionValues",
    "option_values",
    "option1",
    "option2",
    "option3",
    "inventoryItem",
    "inventory_item",
    "inventory_quantity",
    "inventoryQuantity",
    "inventoryQuantities",
    "weight",
    "weight_unit",
    "weightUnit",
    *[alias for aliases in _VARIANT_ALIASES.values() for alias in aliases],
    *[alias for aliases in _INVENTORY_ALIASES.values() for alias in aliases],
}


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _fold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _first_present(
    source: dict[str, Any], aliases: tuple[str, ...]
) -> tuple[bool, Any]:
    for alias in aliases:
        if alias in source:
            return True, source.get(alias)
    return False, None


def _raw_options(product: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (
        product.get("productOptions")
        if isinstance(product.get("productOptions"), list)
        else product.get("options")
    )
    return (
        [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, list)
        else []
    )


def _option_values(option: dict[str, Any]) -> list[str]:
    raw = option.get("values")
    if not isinstance(raw, list):
        raw = option.get("optionValues")
    values: list[str] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            value = item.get("name") if isinstance(item, dict) else item
            text = _text(value)
            if text and _fold(text) not in seen:
                values.append(text)
                seen.add(_fold(text))
    return values


def _variant_pairs(
    variant: dict[str, Any], option_names: list[str]
) -> list[tuple[str, str]]:
    raw = variant.get("selectedOptions") or variant.get("selected_options")
    if not isinstance(raw, list):
        raw = variant.get("optionValues") or variant.get("option_values")
    pairs: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            name = _text(
                item.get("optionName") or item.get("option_name") or item.get("name")
            )
            value = _text(item.get("value") if "value" in item else item.get("name"))
            # selectedOptions uses name/value; optionValues uses optionName/name.
            if "value" in item:
                name = _text(
                    item.get("name")
                    or item.get("optionName")
                    or item.get("option_name")
                )
            if not name and index < len(option_names):
                name = option_names[index]
            if name and value:
                pairs.append((name, value))
    if not pairs:
        for index, option_name in enumerate(option_names[:3], start=1):
            value = _text(variant.get(f"option{index}"))
            if value:
                pairs.append((option_name, value))
    return pairs


def _schema_from_product(product: dict[str, Any]) -> list[str]:
    names = [_text(option.get("name")) for option in _raw_options(product)]
    result = [name for name in names if name]
    if result:
        return result
    raw_variants = product.get("variants")
    if isinstance(raw_variants, dict):
        raw_variants = raw_variants.get("nodes")
    if isinstance(raw_variants, list):
        for variant in raw_variants:
            if not isinstance(variant, dict):
                continue
            pairs = _variant_pairs(variant, [])
            if pairs:
                return [name for name, _ in pairs]
    return []


def _is_placeholder_schema(names: list[str], variants: list[dict[str, Any]]) -> bool:
    if len(names) != 1 or _fold(names[0]) != "title":
        return False
    if not variants:
        return True
    return all(
        len(_variant_pairs(variant, names)) == 1
        and _fold(_variant_pairs(variant, names)[0][1]) == "default title"
        for variant in variants
    )


def _variants(product: dict[str, Any]) -> list[dict[str, Any]]:
    raw = product.get("variants")
    if isinstance(raw, dict):
        raw = raw.get("nodes")
    return (
        [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, list)
        else []
    )


def _canonical_combo(
    variant: dict[str, Any], option_names: list[str]
) -> tuple[str, ...] | None:
    by_name = {
        _fold(name): _fold(value)
        for name, value in _variant_pairs(variant, option_names)
    }
    if not option_names and not by_name:
        return ()
    combo = tuple(by_name.get(_fold(name), "") for name in option_names)
    return combo if all(combo) else None


def _inventory_item(variant: dict[str, Any]) -> dict[str, Any]:
    raw = variant.get("inventoryItem") or variant.get("inventory_item")
    return raw if isinstance(raw, dict) else {}


def _normalized_variant(
    variant: dict[str, Any],
    option_names: list[str],
    *,
    live: bool,
) -> dict[str, Any]:
    pairs = _variant_pairs(variant, option_names)
    by_name = {_fold(name): value for name, value in pairs}
    payload: dict[str, Any] = {
        "optionValues": [
            {"optionName": option_name, "name": by_name[_fold(option_name)]}
            for option_name in option_names
            if _fold(option_name) in by_name
        ]
    }
    if live and _text(variant.get("id")):
        payload["id"] = _text(variant.get("id"))
    for target, aliases in _VARIANT_ALIASES.items():
        present, value = _first_present(variant, aliases)
        if present and value is not None:
            if target in {"price", "compareAtPrice"} and value != "":
                value = str(value)
            if target == "inventoryPolicy" and isinstance(value, str):
                value = value.strip().upper()
            payload[target] = value
    inventory_source = {**_inventory_item(variant), **variant}
    inventory: dict[str, Any] = {}
    for target, aliases in _INVENTORY_ALIASES.items():
        present, value = _first_present(inventory_source, aliases)
        if present and value is not None:
            inventory[target] = (
                str(value) if target == "cost" and value != "" else value
            )
    if live and "cost" not in inventory:
        unit_cost = _inventory_item(variant).get("unitCost")
        amount = unit_cost.get("amount") if isinstance(unit_cost, dict) else None
        if amount is not None:
            inventory["cost"] = str(amount) if amount != "" else amount
    measurement = _inventory_item(variant).get("measurement")
    existing_weight = (
        measurement.get("weight") if isinstance(measurement, dict) else None
    )
    weight = (
        variant.get("weight")
        if "weight" in variant
        else existing_weight.get("value") if isinstance(existing_weight, dict) else None
    )
    unit = variant.get("weight_unit") or variant.get("weightUnit")
    if not unit and isinstance(existing_weight, dict):
        unit = existing_weight.get("unit")
    weight_units = {
        "G": "GRAMS",
        "GRAM": "GRAMS",
        "GRAMS": "GRAMS",
        "KG": "KILOGRAMS",
        "KILOGRAM": "KILOGRAMS",
        "KILOGRAMS": "KILOGRAMS",
        "LB": "POUNDS",
        "LBS": "POUNDS",
        "POUND": "POUNDS",
        "POUNDS": "POUNDS",
        "OZ": "OUNCES",
        "OUNCE": "OUNCES",
        "OUNCES": "OUNCES",
    }
    normalized_unit = weight_units.get(str(unit or "").strip().upper())
    try:
        normalized_weight = float(weight) if weight not in (None, "") else None
    except (TypeError, ValueError):
        normalized_weight = None
    if normalized_weight is not None and normalized_unit:
        inventory["measurement"] = {
            "weight": {"value": normalized_weight, "unit": normalized_unit}
        }
    if inventory:
        payload["inventoryItem"] = inventory
    return payload


def _merge_variant_payload(
    live: dict[str, Any], imported: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(live)
    merged["optionValues"] = imported["optionValues"]
    for field in (
        "sku",
        "price",
        "compareAtPrice",
        "barcode",
        "taxable",
        "taxCode",
        "inventoryPolicy",
    ):
        if field in imported:
            merged[field] = imported[field]
    if "inventoryItem" in imported:
        merged["inventoryItem"] = {
            **merged.get("inventoryItem", {}),
            **imported["inventoryItem"],
        }
        if isinstance(merged["inventoryItem"].get("measurement"), dict) and isinstance(
            imported["inventoryItem"].get("measurement"), dict
        ):
            merged["inventoryItem"]["measurement"] = {
                **live.get("inventoryItem", {}).get("measurement", {}),
                **imported["inventoryItem"]["measurement"],
            }
    return merged


def _normalize_metafields(
    raw: Any,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    if not isinstance(raw, list):
        return [], []
    result: list[dict[str, str]] = []
    blockers: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            blockers.append(
                {"code": "INVALID_METAFIELD", "field": f"metafields[{index}]"}
            )
            continue
        namespace, key = _text(item.get("namespace")), _text(item.get("key"))
        value = item.get("value")
        mf_type = _text(item.get("type")) or "single_line_text_field"
        if not namespace or not key or value is None:
            blockers.append(
                {"code": "INVALID_METAFIELD", "field": f"metafields[{index}]"}
            )
            continue
        pair = (_fold(namespace), _fold(key))
        if pair in seen:
            blockers.append(
                {"code": "DUPLICATE_METAFIELD", "field": f"metafields[{index}]"}
            )
            continue
        seen.add(pair)
        serialized = (
            value
            if isinstance(value, str)
            else __import__("json").dumps(
                value, ensure_ascii=False, separators=(",", ":")
            )
        )
        result.append(
            {"namespace": namespace, "key": key, "value": serialized, "type": mf_type}
        )
    return result, blockers


def merge_structured_product(
    current: dict[str, Any] | None, imported: dict[str, Any], proposed: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the complete option/variant payload and metafield upserts for a plan item."""
    current = current or {}
    blockers: list[dict[str, Any]] = []
    unapplied: list[dict[str, Any]] = []
    imported_variants = _variants(imported)
    live_variants = _variants(current)
    imported_names = _schema_from_product(imported)
    live_names = _schema_from_product(current)

    has_imported_structure = bool(_raw_options(imported) or imported_variants)
    if has_imported_structure:
        if not imported_names:
            if len(imported_variants) == 1:
                imported_names = ["Title"]
            else:
                blockers.append(
                    {"code": "VARIANT_OPTIONS_REQUIRED", "field": "variants"}
                )
        if len(imported_names) > MAX_OPTIONS:
            blockers.append(
                {
                    "code": "OPTION_LIMIT_EXCEEDED",
                    "field": "options",
                    "limit": MAX_OPTIONS,
                }
            )
        if len({_fold(name) for name in imported_names}) != len(imported_names):
            blockers.append({"code": "DUPLICATE_OPTION_NAMES", "field": "options"})

        live_placeholder = _is_placeholder_schema(live_names, live_variants)
        imported_placeholder = (
            len(imported_names) == 1 and _fold(imported_names[0]) == "title"
        )
        if (
            not imported_placeholder
            and not imported_variants
            and (not live_variants or live_placeholder)
        ):
            blockers.append(
                {"code": "VARIANTS_REQUIRED_FOR_OPTIONS", "field": "variants"}
            )
        if (
            live_names
            and not live_placeholder
            and [_fold(x) for x in live_names] != [_fold(x) for x in imported_names]
        ):
            blockers.append({"code": "INCOMPATIBLE_OPTION_SCHEMA", "field": "options"})
        option_names = (
            live_names if live_names and not live_placeholder else imported_names
        )
        if imported_placeholder and live_names and not live_placeholder:
            option_names = live_names

        imported_by_combo: dict[tuple[str, ...], dict[str, Any]] = {}
        for index, variant in enumerate(imported_variants):
            # Deferred source values must survive even when the variant itself
            # is rejected before normalization.
            for key in variant:
                if key not in _VARIANT_SHAPE_KEYS and variant.get(key) not in (
                    None,
                    "",
                    [],
                    {},
                ):
                    unapplied.append(
                        {
                            "code": "UNAPPLIED_VARIANT_ATTRIBUTE",
                            "field": f"variants[{index}].{key}",
                            "value": variant[key],
                        }
                    )
            for key in (
                "inventory_quantity",
                "inventoryQuantity",
                "inventoryQuantities",
            ):
                if key in variant and variant.get(key) not in (None, "", [], {}):
                    unapplied.append(
                        {
                            "code": "TODO_FULFILLMENT_LOCATION",
                            "field": f"variants[{index}].{key}",
                            "value": variant[key],
                        }
                    )
            if imported_placeholder and not _variant_pairs(variant, imported_names):
                variant = {
                    **variant,
                    "optionValues": [{"optionName": "Title", "name": "Default Title"}],
                }
            combo = _canonical_combo(variant, option_names)
            if combo is None:
                blockers.append(
                    {
                        "code": "INCOMPLETE_VARIANT_COMBINATION",
                        "field": f"variants[{index}]",
                    }
                )
                continue
            if combo in imported_by_combo:
                blockers.append(
                    {
                        "code": "DUPLICATE_VARIANT_COMBINATION",
                        "field": f"variants[{index}]",
                    }
                )
                continue
            imported_by_combo[combo] = _normalized_variant(
                variant, option_names, live=False
            )
            if variant.get("weight") not in (None, ""):
                unit = (
                    str(variant.get("weight_unit") or variant.get("weightUnit") or "")
                    .strip()
                    .upper()
                )
                valid_units = {
                    "G",
                    "GRAM",
                    "GRAMS",
                    "KG",
                    "KILOGRAM",
                    "KILOGRAMS",
                    "LB",
                    "LBS",
                    "POUND",
                    "POUNDS",
                    "OZ",
                    "OUNCE",
                    "OUNCES",
                }
                try:
                    float(variant["weight"])
                except (TypeError, ValueError):
                    blockers.append(
                        {
                            "code": "INVALID_VARIANT_WEIGHT",
                            "field": f"variants[{index}].weight",
                        }
                    )
                if unit not in valid_units:
                    blockers.append(
                        {
                            "code": "INVALID_VARIANT_WEIGHT_UNIT",
                            "field": f"variants[{index}].weight_unit",
                        }
                    )
        live_by_combo: dict[tuple[str, ...], dict[str, Any]] = {}
        if not (live_placeholder and not imported_placeholder):
            for index, variant in enumerate(live_variants):
                combo = _canonical_combo(variant, option_names)
                if combo is None or combo in live_by_combo:
                    blockers.append(
                        {
                            "code": "INVALID_LIVE_VARIANT_COMBINATIONS",
                            "field": f"live.variants[{index}]",
                        }
                    )
                    continue
                live_by_combo[combo] = _normalized_variant(
                    variant, option_names, live=True
                )

        merged_variants: list[dict[str, Any]] = []
        for combo, live_variant in live_by_combo.items():
            imported_variant = imported_by_combo.pop(combo, None)
            merged_variants.append(
                _merge_variant_payload(live_variant, imported_variant)
                if imported_variant
                else live_variant
            )
        merged_variants.extend(imported_by_combo.values())
        if len(merged_variants) > MAX_VARIANTS:
            blockers.append(
                {
                    "code": "VARIANT_LIMIT_EXCEEDED",
                    "field": "variants",
                    "limit": MAX_VARIANTS,
                }
            )

        values_by_option: dict[str, list[str]] = {name: [] for name in option_names}
        seen_values: dict[str, set[str]] = {name: set() for name in option_names}
        declared_sources = _raw_options(current) + _raw_options(imported)
        for option in declared_sources:
            matching_name = next(
                (
                    name
                    for name in option_names
                    if _fold(name) == _fold(option.get("name"))
                ),
                None,
            )
            if not matching_name:
                continue
            for value in _option_values(option):
                if _fold(value) not in seen_values[matching_name]:
                    values_by_option[matching_name].append(value)
                    seen_values[matching_name].add(_fold(value))
        for variant in merged_variants:
            for pair in variant.get("optionValues", []):
                name, value = pair["optionName"], pair["name"]
                if _fold(value) not in seen_values[name]:
                    values_by_option[name].append(value)
                    seen_values[name].add(_fold(value))
        proposed["productOptions"] = [
            {
                "name": name,
                "position": index + 1,
                "values": [{"name": value} for value in values_by_option[name]],
            }
            for index, name in enumerate(option_names)
        ]
        proposed["variants"] = merged_variants

    imported_metafields, mf_blockers = _normalize_metafields(imported.get("metafields"))
    blockers.extend(mf_blockers)
    if imported_metafields:
        proposed["metafields"] = imported_metafields
    return proposed, blockers, unapplied


def validate_product(
    product: dict[str, Any], *, creating: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    unapplied: list[dict[str, Any]] = []
    for field, code in DEFERRED_FIELDS.items():
        if product.get(field) not in (None, "", [], {}):
            unapplied.append({"code": code, "field": field, "value": product[field]})
    if creating and not _text(product.get("title")):
        blockers.append({"code": "TITLE_REQUIRED", "field": "title"})
    handle = _text(product.get("handle"))
    if handle and not HANDLE_RE.fullmatch(handle):
        blockers.append({"code": "INVALID_HANDLE", "field": "handle"})
    status = _text(product.get("status")) or ("DRAFT" if creating else None)
    if status and status.upper() not in VALID_STATUSES:
        blockers.append({"code": "INVALID_STATUS", "field": "status"})
    return blockers, unapplied


def merge_tags(current: Any, proposed: Any) -> list[str]:
    def values(raw: Any) -> list[str]:
        if isinstance(raw, str):
            return [x.strip() for x in raw.split(",") if x.strip()]
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return []

    result: list[str] = []
    seen: set[str] = set()
    for tag in values(current) + values(proposed):
        if tag.casefold() not in seen:
            result.append(tag)
            seen.add(tag.casefold())
    return result


def preserve_blank_fields(
    current: dict[str, Any], proposed: dict[str, Any]
) -> dict[str, Any]:
    result = canonical_product(proposed)
    current = canonical_product(current)
    for field in SUPPORTED_PRODUCT_FIELDS - {"tags"}:
        if result.get(field) in (None, "") and current.get(field) not in (None, ""):
            result[field] = current[field]
    if isinstance(current.get("seo"), dict) and isinstance(result.get("seo"), dict):
        result["seo"] = {**current["seo"], **result["seo"]}
    result["tags"] = merge_tags(current.get("tags"), proposed.get("tags"))
    return result


def canonical_product(product: dict[str, Any]) -> dict[str, Any]:
    """Return only server-approved scalar productSet fields in a stable representation."""
    return normalize_core_product(product)


def _structured_summary(field: str, product: dict[str, Any]) -> dict[str, int]:
    if field == "options":
        options = _raw_options(product)
        return {
            "options": len(options),
            "values": sum(len(_option_values(option)) for option in options),
        }
    if field == "variants":
        return {"variants": len(_variants(product))}
    metafields, _ = _normalize_metafields(product.get("metafields"))
    return {"metafields": len(metafields)}


def applicable_changes(
    current: dict[str, Any] | None, proposed: dict[str, Any]
) -> list[dict[str, Any]]:
    current = current or {}
    before = canonical_product(current)
    after = canonical_product(proposed)
    changes = [
        {"field": field, "current": before.get(field), "proposed": after.get(field)}
        for field in sorted(SUPPORTED_PRODUCT_FIELDS)
        if before.get(field) != after.get(field)
    ]
    if "productOptions" in proposed:
        before_options = _raw_options(current)
        if _structured_summary("options", current) != _structured_summary(
            "options", {"productOptions": proposed["productOptions"]}
        ) or [
            (_fold(x.get("name")), [_fold(v) for v in _option_values(x)])
            for x in before_options
        ] != [
            (_fold(x.get("name")), [_fold(v) for v in _option_values(x)])
            for x in proposed["productOptions"]
        ]:
            changes.append(
                {
                    "field": "options",
                    "current": _structured_summary("options", current),
                    "proposed": _structured_summary(
                        "options", {"productOptions": proposed["productOptions"]}
                    ),
                }
            )
    if "variants" in proposed:
        before_variants = [
            _normalized_variant(v, _schema_from_product(current), live=True)
            for v in _variants(current)
        ]
        if before_variants != proposed["variants"]:
            changes.append(
                {
                    "field": "variants",
                    "current": {"variants": len(before_variants)},
                    "proposed": {"variants": len(proposed["variants"])},
                }
            )
    if "metafields" in proposed:
        live_by_key = (
            {
                (_fold(x.get("namespace")), _fold(x.get("key"))): x
                for x in current.get("metafields", {}).get("nodes", [])
                if isinstance(x, dict)
            }
            if isinstance(current.get("metafields"), dict)
            else {}
        )
        changed = [
            mf
            for mf in proposed["metafields"]
            if any(
                str(
                    live_by_key.get((_fold(mf["namespace"]), _fold(mf["key"])), {}).get(
                        k
                    )
                    or ""
                )
                != str(mf[k])
                for k in ("value", "type")
            )
        ]
        if changed:
            changes.append(
                {
                    "field": "metafields",
                    "current": {"matching": len(proposed["metafields"]) - len(changed)},
                    "proposed": {"upserts": len(changed)},
                }
            )
    return changes


def _variant_subject(variant: dict[str, Any], option_names: list[str]) -> str:
    pairs = _variant_pairs(variant, option_names)
    if pairs:
        return " / ".join(f"{name}: {value}" for name, value in pairs)
    return (
        _text(variant.get("title"))
        or _text(variant.get("id"))
        or _text(variant.get("sku"))
        or "Variant"
    )


def _variant_subject_identity(
    variant: dict[str, Any], option_names: list[str]
) -> dict[str, Any] | None:
    combo = _canonical_combo(variant, option_names)
    if combo is None:
        return None
    return {
        "kind": "variant_options",
        "option_names": [_fold(name) for name in option_names],
        "values": list(combo),
    }


def _flatten_variant(variant: dict[str, Any]) -> dict[str, Any]:
    inventory = (
        variant.get("inventoryItem")
        if isinstance(variant.get("inventoryItem"), dict)
        else {}
    )
    measurement = (
        inventory.get("measurement")
        if isinstance(inventory.get("measurement"), dict)
        else {}
    )
    weight = (
        measurement.get("weight") if isinstance(measurement.get("weight"), dict) else {}
    )
    values = {
        field: variant.get(field)
        for field in (
            "price",
            "compareAtPrice",
            "sku",
            "barcode",
            "taxable",
            "taxCode",
            "inventoryPolicy",
        )
        if field in variant
    }
    values.update(
        {
            f"inventoryItem.{field}": inventory.get(field)
            for field in ("cost", "tracked", "requiresShipping")
            if field in inventory
        }
    )
    if "value" in weight:
        values["inventoryItem.weight.value"] = weight.get("value")
    if "unit" in weight:
        values["inventoryItem.weight.unit"] = weight.get("unit")
    return values


def _review_row(
    *,
    scope: str,
    subject: str,
    field: str,
    current: Any,
    proposed: Any,
    disposition: str,
    change_kind: str,
    value_type: str | None = None,
    reason_codes: list[str] | None = None,
    subject_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_value_type = value_type or (
        "money"
        if field in {"price", "compareAtPrice", "inventoryItem.cost"}
        else (
            "weight"
            if field.startswith("inventoryItem.weight")
            else (
                "boolean"
                if isinstance(current, bool) or isinstance(proposed, bool)
                else (
                    "json"
                    if isinstance(current, (dict, list))
                    or isinstance(proposed, (dict, list))
                    else "text"
                )
            )
        )
    )
    enum_values = {
        "status": ["ACTIVE", "ARCHIVED", "DRAFT", "UNLISTED"],
        "inventoryPolicy": ["DENY", "CONTINUE"],
        "inventoryItem.weight.unit": ["GRAMS", "KILOGRAMS", "OUNCES", "POUNDS"],
    }.get(field)
    editor = "text"
    edit_schema: dict[str, Any] = {"editor": editor, "required": field in {"title", "handle"}}
    if field == "values" and scope == "option":
        edit_schema = {
            "editor": "option_values",
            "required": True,
            "item_type": "text",
        }
    elif field in {"tags", "values"}:
        edit_schema = {"editor": "list", "required": False, "item_type": "text"}
    elif resolved_value_type in {"money", "weight"} and field != "inventoryItem.weight.unit":
        edit_schema = {"editor": "decimal", "required": False, "minimum": 0}
    elif resolved_value_type == "boolean":
        edit_schema = {"editor": "boolean", "required": False, "options": [True, False]}
    elif enum_values:
        edit_schema = {"editor": "enum", "required": False, "options": enum_values}
    elif resolved_value_type == "json":
        edit_schema = {"editor": "json", "required": False}
    elif resolved_value_type == "metafield" and isinstance(proposed, str):
        try:
            parsed_metafield = __import__("json").loads(proposed)
        except (TypeError, ValueError):
            parsed_metafield = None
        if isinstance(parsed_metafield, (dict, list)):
            edit_schema = {"editor": "json", "required": False, "preserve_string": True}
    if field == "handle":
        edit_schema["format"] = "handle"
    row = {
        "scope": scope,
        "subject": subject,
        "field": field,
        "current": current,
        "proposed": proposed,
        "disposition": disposition,
        "change_kind": change_kind,
        "value_type": resolved_value_type,
        "edit_schema": edit_schema,
    }
    if reason_codes:
        row["reason_codes"] = reason_codes
    if isinstance(subject_identity, dict):
        row["subject_identity"] = deepcopy(subject_identity)
    return row


def _normalized_path_part(value: Any) -> str:
    """Backward-compatible import for callers; canonical rules live in one module."""
    return normalized_path_part(value)


def _value_path(row: dict[str, Any]) -> str:
    scope = str(row.get("scope") or "product")
    field = str(row.get("field") or "value")
    if scope == "product":
        return field
    return f"{scope}[{_normalized_path_part(row.get('subject'))}].{field}"


def _normalized_origin_path(value: Any) -> str:
    return canonical_value_path(value)


def _matching_value_origin(
    origins: dict[str, Any], row: dict[str, Any]
) -> str | None:
    """Resolve one exact value origin, tolerating equivalent path spellings."""
    value_path = _value_path(row)
    requested = origins.get(value_path)
    if requested in VALUE_ORIGINS:
        return str(requested)

    normalized_path = _normalized_origin_path(value_path)
    matching_items = [
        origin for path, origin in origins.items()
        if _normalized_origin_path(path) == normalized_path
    ]
    if matching_items and not any(origin in VALUE_ORIGINS for origin in matching_items):
        return "unknown"
    matches = {
        str(origin)
        for path, origin in origins.items()
        if origin in VALUE_ORIGINS
        and _normalized_origin_path(path) == normalized_path
    }
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _source_ref_identity(ref: dict[str, Any]) -> tuple[Any, ...]:
    bbox = ref.get("bbox")
    return (
        ref.get("source_file_id"),
        ref.get("sheet"),
        ref.get("cell"),
        ref.get("cell_range"),
        ref.get("page"),
        tuple(bbox) if isinstance(bbox, list) else None,
        ref.get("evidence_role", "value"),
    )


def _matching_source_refs(
    imported: dict[str, Any], row: dict[str, Any]
) -> list[dict[str, Any]]:
    refs = imported.get("source_refs")
    if not isinstance(refs, list):
        return []
    value_path = _value_path(row)
    exact = [
        deepcopy(ref) for ref in refs
        if isinstance(ref, dict)
        and ref.get("value_path")
        and canonical_value_path(ref.get("value_path")) == canonical_value_path(value_path)
    ]
    if exact:
        deduplicated: dict[tuple[Any, ...], dict[str, Any]] = {}
        for ref in exact:
            ref.setdefault("evidence_role", "value")
            deduplicated.setdefault(_source_ref_identity(ref), ref)
        return list(deduplicated.values())
    # Field-only fallback exists solely for unambiguous legacy product values.
    if str(row.get("scope") or "product") != "product":
        return []
    field = str(row.get("field") or "")
    aliases = {field, field.split(".")[-1]}
    if field.startswith("seo."):
        aliases.add(f"seo_{field.split('.', 1)[1]}")
    if field == "descriptionHtml":
        aliases.add("body_html")
    if field == "productType":
        aliases.update({"product_type", "product_category"})
    if field == "inventoryItem.cost":
        aliases.update({"cost", "cost_per_item"})
    legacy = [
        ref for ref in refs
        if isinstance(ref, dict)
        and not ref.get("value_path")
        and str(ref.get("field") or "") in aliases
    ]
    if len(legacy) != 1:
        return []
    ref = deepcopy(legacy[0])
    ref.setdefault("evidence_role", "value")
    return [ref]


def _decorate_review_rows(
    rows: list[dict[str, Any]], imported: dict[str, Any]
) -> list[dict[str, Any]]:
    """Attach stable identity, inclusion, and value-level provenance contracts."""
    product_identity = (
        imported.get("shopify_gid")
        or imported.get("id")
        or imported.get("handle")
        or imported.get("title")
        or "product"
    )
    raw_origins = imported.get("value_origins") or imported.get("_value_origins")
    origins = raw_origins if isinstance(raw_origins, dict) else {}
    for row in rows:
        value_path = _value_path(row)
        source_refs = _matching_source_refs(imported, row)
        source_ref = next(
            (
                ref for ref in source_refs
                if ref.get("evidence_role", "value") == "value"
            ),
            None,
        )
        requested_origin = _matching_value_origin(origins, row)
        origin = requested_origin if requested_origin else (
            "source" if source_ref else "unknown"
        )
        disposition = str(row.get("disposition") or "applicable")
        required = bool(
            disposition == "applicable"
            and (
                (row.get("scope") == "product" and row.get("field") == "title" and row.get("current") is None)
                or row.get("scope") == "option"
                or row.get("field") == "variant"
            )
        )
        if isinstance(row.get("edit_schema"), dict) and row["edit_schema"].get("required"):
            required = True
        identity = "\x1f".join(
            (
                str(product_identity).strip().casefold(),
                str(row.get("scope") or "").strip().casefold(),
                str(row.get("subject") or "").strip().casefold(),
                value_path.casefold(),
            )
        )
        row.update(
            {
                "change_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
                "value_path": value_path,
                "origin": origin,
                "included": disposition == "applicable",
                "selectable": disposition == "applicable" and not required,
                "required": required,
            }
        )
        if source_refs:
            row["source_refs"] = source_refs
        else:
            row.pop("source_refs", None)
        if source_ref and origin == "source":
            row["source_ref"] = source_ref
        else:
            row.pop("source_ref", None)
    return rows


def _variant_targets_for_review_row(
    product: dict[str, Any], row: dict[str, Any]
) -> list[dict[str, Any]]:
    option_names = _schema_from_product(product)
    identity = row.get("subject_identity")
    expected_identity: dict[str, Any] | None = None
    if (
        isinstance(identity, dict)
        and identity.get("kind") == "variant_options"
        and isinstance(identity.get("option_names"), list)
        and isinstance(identity.get("values"), list)
    ):
        expected_identity = {
            "kind": "variant_options",
            "option_names": [_fold(name) for name in identity["option_names"]],
            "values": [_fold(value) for value in identity["values"]],
        }
        if expected_identity["option_names"] != [
            _fold(name) for name in option_names
        ]:
            return []
    subject = str(row.get("subject") or "")
    matches: list[dict[str, Any]] = []
    for variant in _variants(product):
        if expected_identity is not None:
            if _variant_subject_identity(variant, option_names) == expected_identity:
                matches.append(variant)
        elif _variant_subject(variant, option_names) == subject:
            matches.append(variant)
    return matches


def _unique_variant_target(
    product: dict[str, Any], row: dict[str, Any]
) -> dict[str, Any] | None:
    matches = _variant_targets_for_review_row(product, row)
    return matches[0] if len(matches) == 1 else None


def _review_row_target_exists(item: dict[str, Any], row: dict[str, Any]) -> bool:
    """Return whether a review row has a canonical mutation target."""
    proposed = item.get("proposed")
    if not isinstance(proposed, dict):
        return False
    scope = str(row.get("scope") or "product").strip().casefold()
    subject = str(row.get("subject") or "")
    if scope == "product":
        return True
    if scope == "option":
        options = proposed.get("productOptions")
        return isinstance(options, list) and any(
            isinstance(option, dict)
            and _fold(option.get("name")) == _fold(subject)
            for option in options
        )
    if scope in {"variant", "inventory"}:
        return _unique_variant_target(proposed, row) is not None
    if scope == "metafield":
        metafields = proposed.get("metafields")
        return isinstance(metafields, list) and any(
            isinstance(metafield, dict)
            and _fold(
                f"{metafield.get('namespace')}.{metafield.get('key')}"
            )
            == _fold(subject)
            for metafield in metafields
        )
    return False


def _technical_review_row(row: dict[str, Any]) -> bool:
    if str(row.get("scope") or "").strip().casefold() != "metafield":
        return False
    subject = str(row.get("subject") or "").strip().casefold()
    namespace = subject.split(".", 1)[0]
    field = str(row.get("field") or "").strip().casefold()
    return (
        namespace == "extractor"
        or field == "type"
        or "confidence" in subject
    )


def normalize_review_plan_rows(plan: dict[str, Any]) -> dict[str, Any]:
    """Hydrate the additive v2 row presentation and edit-safety contract.

    Classification is recomputed from mutation authority instead of trusting
    persisted client-facing flags. This makes stored v2 plans safe by default
    and prevents a missing or stale ``editable`` value from granting access.
    """
    normalized = deepcopy(plan)
    if normalized.get("review_contract_version") not in {2, 3}:
        return normalized
    items = normalized.get("items")
    if not isinstance(items, list):
        return normalized
    for item in items:
        if not isinstance(item, dict):
            continue
        rows = item.get("review_changes")
        if not isinstance(rows, list):
            continue
        identity_candidates: dict[str, dict[str, dict[str, Any]]] = {}
        for candidate_product in (
            item.get("proposed"),
            item.get("live_snapshot"),
        ):
            if not isinstance(candidate_product, dict):
                continue
            option_names = _schema_from_product(candidate_product)
            for variant in _variants(candidate_product):
                subject = _variant_subject(variant, option_names)
                identity = _variant_subject_identity(variant, option_names)
                if not isinstance(identity, dict):
                    continue
                identity_key = json.dumps(
                    identity, sort_keys=True, separators=(",", ":")
                )
                identity_candidates.setdefault(subject, {})[
                    identity_key
                ] = identity
        for row in rows:
            if not isinstance(row, dict):
                continue
            if (
                row.get("scope") in {"variant", "inventory"}
                and not isinstance(row.get("subject_identity"), dict)
            ):
                matches = identity_candidates.get(
                    str(row.get("subject") or ""), {}
                )
                if len(matches) == 1:
                    row["subject_identity"] = deepcopy(
                        next(iter(matches.values()))
                    )
            disposition = str(row.get("disposition") or "applicable")
            target_exists = _review_row_target_exists(item, row)
            if (
                target_exists
                and row.get("scope") == "option"
                and row.get("field") == "values"
            ):
                row["edit_schema"] = {
                    "editor": "option_values",
                    "required": True,
                    "item_type": "text",
                }
                row["option_value_impacts"] = _option_value_impacts(
                    item.get("live_snapshot")
                    if isinstance(item.get("live_snapshot"), dict)
                    else None,
                    item.get("proposed")
                    if isinstance(item.get("proposed"), dict)
                    else {},
                    str(row.get("subject") or ""),
                )
            if disposition == "applicable" and not target_exists:
                presentation = "hidden"
            elif _technical_review_row(row):
                presentation = "technical"
            else:
                presentation = "merchant"
            row["presentation"] = presentation
            row["editable"] = bool(
                presentation == "merchant"
                and disposition in {"applicable", "blocked"}
                and target_exists
            )
    return normalized


def _set_or_remove(container: dict[str, Any], path: str, value: Any, *, present: bool) -> None:
    parts = path.split(".")
    target = container
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            if not present:
                return
            child = {}
            target[part] = child
        target = child
    if present:
        target[parts[-1]] = deepcopy(value)
    else:
        target.pop(parts[-1], None)


def materialize_execution_proposed(item: dict[str, Any]) -> dict[str, Any]:
    """Build the immutable Shopify payload from only approved review rows."""
    proposed = deepcopy(item.get("proposed") or {})
    current = item.get("live_snapshot") if isinstance(item.get("live_snapshot"), dict) else {}
    rows = item.get("review_changes") if isinstance(item.get("review_changes"), list) else []
    option_names = _schema_from_product({"options": proposed.get("productOptions", [])}) or _schema_from_product(current)

    for row in rows:
        if not isinstance(row, dict) or row.get("included") or row.get("disposition") != "applicable":
            continue
        scope = str(row.get("scope") or "product")
        field = str(row.get("field") or "")
        subject = str(row.get("subject") or "")
        if scope == "product":
            _set_or_remove(proposed, field, row.get("current"), present=row.get("current") is not None)
            continue
        if scope == "option":
            # Option values are structural dependencies and should have been rejected
            # by selection validation. Keep this guard for historical plans.
            continue
        if scope in {"variant", "inventory"}:
            target = _unique_variant_target(proposed, row)
            if isinstance(target, dict):
                _set_or_remove(target, field, row.get("current"), present=row.get("current") is not None)
            continue
        if scope == "metafield":
            candidates = proposed.get("metafields") if isinstance(proposed.get("metafields"), list) else []
            target = next(
                (
                    metafield
                    for metafield in candidates
                    if isinstance(metafield, dict)
                    and f"{metafield.get('namespace')}.{metafield.get('key')}" == subject
                ),
                None,
            )
            if isinstance(target, dict):
                _set_or_remove(target, field, row.get("current"), present=row.get("current") is not None)
    return proposed


def approved_value_provenance(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        str(item.get("item_id")): {
            str(row.get("value_path")): {
                "origin": row.get("origin", "unknown"),
                **({"source_ref": deepcopy(row["source_ref"])} if isinstance(row.get("source_ref"), dict) else {}),
            }
            for row in item.get("review_changes", [])
            if isinstance(row, dict) and row.get("value_path")
        }
        for item in plan.get("items", [])
        if isinstance(item, dict)
    }


def _kind(current: Any, proposed: Any) -> str:
    if current is None and proposed is not None:
        return "added"
    if current is not None and proposed is None:
        return "removed"
    return "updated"


def review_changes(
    current: dict[str, Any] | None,
    imported: dict[str, Any],
    proposed: dict[str, Any],
    blockers: list[dict[str, Any]] | None = None,
    unapplied: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Project the mutation authority into exact, display-only audit rows."""
    current = current or {}
    blockers = blockers or []
    unapplied = unapplied or []
    reason_codes = list(
        dict.fromkeys(
            str(item.get("code"))
            for item in blockers
            if isinstance(item, dict) and item.get("code")
        )
    )
    disposition = "blocked" if blockers else "applicable"
    rows: list[dict[str, Any]] = []

    before = canonical_product(current)
    after = canonical_product(proposed)
    for field in sorted(SUPPORTED_PRODUCT_FIELDS):
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        if field == "seo" and isinstance(old, dict) and isinstance(new, dict):
            for seo_field in ("title", "description"):
                if old.get(seo_field) != new.get(seo_field):
                    rows.append(
                        _review_row(
                            scope="product",
                            subject=str(
                                after.get("title") or before.get("title") or "Product"
                            ),
                            field=f"seo.{seo_field}",
                            current=old.get(seo_field),
                            proposed=new.get(seo_field),
                            disposition=disposition,
                            change_kind=_kind(old.get(seo_field), new.get(seo_field)),
                            reason_codes=reason_codes,
                        )
                    )
            continue
        rows.append(
            _review_row(
                scope="product",
                subject=str(after.get("title") or before.get("title") or "Product"),
                field=field,
                current=old,
                proposed=new,
                disposition=disposition,
                change_kind=_kind(old, new),
                reason_codes=reason_codes,
            )
        )

    if "productOptions" in proposed:
        old_options = {_fold(x.get("name")): x for x in _raw_options(current)}
        new_options = {_fold(x.get("name")): x for x in proposed["productOptions"]}
        for key in dict.fromkeys([*old_options, *new_options]):
            old_option, new_option = old_options.get(key), new_options.get(key)
            old_values = _option_values(old_option or {}) if old_option else None
            new_values = _option_values(new_option or {}) if new_option else None
            if old_values == new_values:
                continue
            name = _text((new_option or old_option or {}).get("name")) or "Option"
            rows.append(
                _review_row(
                    scope="option",
                    subject=name,
                    field="values",
                    current=old_values,
                    proposed=new_values,
                    disposition=disposition,
                    change_kind=_kind(old_option, new_option),
                    reason_codes=reason_codes,
                )
            )

    option_names = _schema_from_product({"options": proposed.get("productOptions", [])})
    if not option_names:
        option_names = _schema_from_product(current)
    if "variants" in proposed:
        old_variants = [
            _normalized_variant(item, option_names, live=True)
            for item in _variants(current)
        ]
        new_variants = [item for item in proposed["variants"] if isinstance(item, dict)]
        unmatched_old = set(range(len(old_variants)))
        matches: list[tuple[dict[str, Any] | None, dict[str, Any]]] = []
        for new_variant in new_variants:
            matched_index = next(
                (
                    index
                    for index in unmatched_old
                    if new_variant.get("id")
                    and old_variants[index].get("id") == new_variant.get("id")
                ),
                None,
            )
            if matched_index is None:
                new_combo = _canonical_combo(new_variant, option_names)
                matched_index = next(
                    (
                        index
                        for index in unmatched_old
                        if new_combo is not None
                        and _canonical_combo(old_variants[index], option_names)
                        == new_combo
                    ),
                    None,
                )
            old_variant = (
                old_variants[matched_index] if matched_index is not None else None
            )
            if matched_index is not None:
                unmatched_old.remove(matched_index)
            matches.append((old_variant, new_variant))
        matches.extend((old_variants[index], {}) for index in sorted(unmatched_old))
        for old_variant, new_variant in matches:
            old_values = _flatten_variant(old_variant or {})
            new_values = _flatten_variant(new_variant)
            subject = _variant_subject(new_variant or old_variant or {}, option_names)
            fields = list(dict.fromkeys([*old_values, *new_values]))
            if not fields and old_variant is None:
                fields = ["variant"]
            for field in fields:
                old, new = old_values.get(field), new_values.get(field)
                if old == new:
                    continue
                rows.append(
                    _review_row(
                        scope="variant",
                        subject=subject,
                        field=field,
                        current=old,
                        proposed=new,
                        disposition=disposition,
                        change_kind=_kind(old_variant, new_variant or None),
                        reason_codes=reason_codes,
                        subject_identity=_variant_subject_identity(
                            new_variant or old_variant or {}, option_names
                        ),
                    )
                )

    # Missing required commerce values still need concrete editable rows. They
    # remain blockers until the merchant supplies them during review.
    proposed_variants = _variants(proposed)
    for blocker in blockers:
        if not isinstance(blocker, dict) or blocker.get("code") not in {
            "SKU_REQUIRED", "PRICE_REQUIRED"
        }:
            continue
        match = re.fullmatch(
            r"variants\[(\d+)\]\.(sku|price)", str(blocker.get("field") or "")
        )
        if not match:
            continue
        variant_index, field = int(match.group(1)), match.group(2)
        if variant_index >= len(proposed_variants):
            continue
        variant = proposed_variants[variant_index]
        subject = _variant_subject(variant, option_names)
        identity = _variant_subject_identity(variant, option_names)
        if any(
            row.get("scope") == "variant"
            and row.get("field") == field
            and row.get("subject_identity") == identity
            for row in rows
        ):
            continue
        missing_row = _review_row(
            scope="variant", subject=subject, field=field, current=None,
            proposed=None, disposition="blocked", change_kind="added",
            reason_codes=[str(blocker["code"])], subject_identity=identity,
        )
        missing_row["edit_schema"]["required"] = True
        rows.append(missing_row)

    if "metafields" in proposed:
        old_metafields = {
            (_fold(x.get("namespace")), _fold(x.get("key"))): x
            for x in (
                current.get("metafields", {}).get("nodes", [])
                if isinstance(current.get("metafields"), dict)
                else []
            )
            if isinstance(x, dict)
        }
        for metafield in proposed["metafields"]:
            key = (_fold(metafield.get("namespace")), _fold(metafield.get("key")))
            old = old_metafields.get(key, {})
            subject = f"{metafield['namespace']}.{metafield['key']}"
            for field in ("value", "type"):
                if old.get(field) == metafield.get(field):
                    continue
                rows.append(
                    _review_row(
                        scope="metafield",
                        subject=subject,
                        field=field,
                        current=old.get(field),
                        proposed=metafield.get(field),
                        disposition=disposition,
                        change_kind=_kind(old or None, metafield),
                        value_type="metafield" if field == "value" else "text",
                        reason_codes=reason_codes,
                    )
                )

    # Invalid or duplicate extracted variants may be absent from `proposed`.
    # Preserve their rejected values so a blocked plan never hides merchant data.
    imported_names = _schema_from_product(imported) or option_names
    proposed_combos = {
        _canonical_combo(item, option_names)
        for item in proposed.get("variants", [])
        if isinstance(item, dict)
    }
    proposed_ids = {
        item.get("id")
        for item in proposed.get("variants", [])
        if isinstance(item, dict) and item.get("id")
    }
    for index, raw_variant in enumerate(_variants(imported)):
        targeted_codes = [
            str(item.get("code"))
            for item in blockers
            if isinstance(item, dict)
            and str(item.get("field") or "").startswith(f"variants[{index}]")
            and item.get("code")
        ]
        combo = _canonical_combo(raw_variant, option_names or imported_names)
        represented = bool(raw_variant.get("id") in proposed_ids) or (
            combo is not None and combo in proposed_combos
        )
        if not targeted_codes and represented:
            continue
        normalized = _normalized_variant(raw_variant, imported_names, live=False)
        for field, value in _flatten_variant(normalized).items():
            rows.append(
                _review_row(
                    scope="variant",
                    subject=_variant_subject(raw_variant, imported_names),
                    field=field,
                    current=None,
                    proposed=value,
                    disposition="blocked",
                    change_kind="rejected",
                    reason_codes=list(dict.fromkeys(targeted_codes or reason_codes)),
                    subject_identity=_variant_subject_identity(
                        raw_variant, imported_names
                    ),
                )
            )

    for entry in unapplied:
        if not isinstance(entry, dict):
            continue
        field = str(entry.get("field") or entry.get("code") or "value")
        variant_match = re.match(r"variants\[(\d+)\]\.(.+)", field)
        subject = str(after.get("title") or before.get("title") or "Product")
        scope = "product"
        canonical_field = field
        if variant_match:
            index = int(variant_match.group(1))
            imported_variants = _variants(imported)
            variant = imported_variants[index] if index < len(imported_variants) else {}
            subject = _variant_subject(variant, imported_names)
            scope = "inventory" if "quantity" in field.casefold() else "variant"
            canonical_field = variant_match.group(2)
        subject_identity = (
            _variant_subject_identity(variant, imported_names)
            if variant_match
            else None
        )
        rows.append(
            _review_row(
                scope=scope,
                subject=subject,
                field=canonical_field,
                current=None,
                proposed=entry.get("value"),
                disposition="deferred",
                change_kind="deferred",
                reason_codes=[str(entry.get("code"))] if entry.get("code") else None,
                subject_identity=subject_identity,
            )
        )
    return _decorate_review_rows(rows, imported)


class OptionValueEditError(ValueError):
    """Structured validation failure raised by pure option-value transforms."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require_unique_option_values(values: list[str]) -> None:
    if not values or any(not _text(value) for value in values):
        raise OptionValueEditError(
            "BLANK_OPTION_VALUE", "Option values cannot be blank."
        )
    if len({_fold(value) for value in values}) != len(values):
        raise OptionValueEditError(
            "DUPLICATE_OPTION_VALUE",
            "Option values must be unique, ignoring capitalization.",
        )


def _raw_option_value_names(option: dict[str, Any]) -> list[str]:
    raw = option.get("values")
    if not isinstance(raw, list):
        raw = option.get("optionValues")
    if not isinstance(raw, list):
        raise OptionValueEditError(
            "BLANK_OPTION_VALUE", "An option must contain at least one value."
        )
    names: list[str] = []
    for entry in raw:
        value = entry.get("name") if isinstance(entry, dict) else entry
        names.append(str(value).strip() if value is not None else "")
    return names


def _require_raw_option_values(option: dict[str, Any]) -> list[str]:
    values = _raw_option_value_names(option)
    _require_unique_option_values(values)
    return values


def _resolve_option(
    product: dict[str, Any], option_name: str
) -> tuple[int, dict[str, Any]]:
    matches = [
        (index, option)
        for index, option in enumerate(_raw_options(product))
        if _fold(option.get("name")) == _fold(option_name)
    ]
    if not matches:
        raise OptionValueEditError(
            "OPTION_TARGET_MISSING", "This option is no longer available."
        )
    if len(matches) != 1:
        raise OptionValueEditError(
            "AMBIGUOUS_OPTION_TARGET",
            "This option cannot be edited because its name is ambiguous.",
        )
    return matches[0]


def _replace_option_values(
    option: dict[str, Any],
    values: list[str],
    *,
    renamed_from: str | None = None,
    renamed_to: str | None = None,
) -> None:
    key = "values" if isinstance(option.get("values"), list) else "optionValues"
    raw = option.get(key) if isinstance(option.get(key), list) else []
    by_fold: dict[str, Any] = {}
    for entry in raw:
        name = entry.get("name") if isinstance(entry, dict) else entry
        if _text(name):
            by_fold[_fold(name)] = deepcopy(entry)
    if renamed_from and renamed_to and _fold(renamed_from) in by_fold:
        entry = by_fold.pop(_fold(renamed_from))
        if isinstance(entry, dict):
            entry["name"] = renamed_to
        else:
            entry = renamed_to
        by_fold[_fold(renamed_to)] = entry
    result: list[Any] = []
    uses_objects = key == "optionValues" or any(
        isinstance(entry, dict) for entry in raw
    )
    for value in values:
        entry = deepcopy(by_fold.get(_fold(value)))
        if isinstance(entry, dict):
            entry["name"] = value
            result.append(entry)
        elif entry is not None and not uses_objects:
            result.append(value)
        else:
            result.append({"name": value} if uses_objects else value)
    option[key] = result


def _variant_value_map(
    variant: dict[str, Any], option_names: list[str]
) -> dict[str, str]:
    return {
        _fold(name): value for name, value in _variant_pairs(variant, option_names)
    }


def _set_variant_option_value(
    variant: dict[str, Any],
    option_names: list[str],
    option_index: int,
    option_name: str,
    value: str,
) -> None:
    positional = f"option{option_index + 1}"
    if positional in variant or not any(
        isinstance(variant.get(key), list)
        for key in ("selectedOptions", "selected_options", "optionValues", "option_values")
    ):
        variant[positional] = value
    for key in ("selectedOptions", "selected_options", "optionValues", "option_values"):
        pairs = variant.get(key)
        if not isinstance(pairs, list):
            continue
        matched = False
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            pair_name = (
                pair.get("name")
                if "value" in pair
                else pair.get("optionName") or pair.get("option_name") or pair.get("name")
            )
            if _fold(pair_name) != _fold(option_name):
                continue
            pair["value" if "value" in pair else "name"] = value
            matched = True
        if not matched:
            if key in {"selectedOptions", "selected_options"}:
                pairs.append({"name": option_name, "value": value})
            else:
                pairs.append({"optionName": option_name, "name": value})


def _commerce_clone(variant: dict[str, Any]) -> dict[str, Any]:
    """Clone only option references and safe commerce defaults."""
    top_level = {
        "selectedOptions",
        "selected_options",
        "optionValues",
        "option_values",
        "option1",
        "option2",
        "option3",
        "price",
        "compareAtPrice",
        "compare_at_price",
        "compare_at",
        "taxable",
        "taxCode",
        "tax_code",
        "variant_tax_code",
        "inventoryPolicy",
        "inventory_policy",
        "inventory_tracker",
        "requiresShipping",
        "requires_shipping",
        "fulfillment_service",
        "tracked",
        "inventory_tracked",
        "track_inventory",
        "weight",
        "weight_unit",
        "weightUnit",
        "variant_weight_unit",
        "grams",
        "cost",
        "unit_cost",
        "cost_per_item",
    }
    clone = {
        key: deepcopy(value)
        for key, value in variant.items()
        if key in top_level
    }
    inventory_source = variant.get("inventoryItem")
    inventory_key = "inventoryItem"
    if not isinstance(inventory_source, dict):
        inventory_source = variant.get("inventory_item")
        inventory_key = "inventory_item"
    if isinstance(inventory_source, dict):
        inventory: dict[str, Any] = {}
        for key in (
            "cost",
            "unit_cost",
            "cost_per_item",
            "unitCost",
            "tracked",
            "inventory_tracked",
            "track_inventory",
            "requiresShipping",
            "requires_shipping",
            "measurement",
        ):
            if key in inventory_source:
                inventory[key] = deepcopy(inventory_source[key])
        if inventory:
            clone[inventory_key] = inventory

    def scrub(value: Any) -> Any:
        if isinstance(value, list):
            return [scrub(entry) for entry in value]
        if not isinstance(value, dict):
            return value
        cleaned: dict[str, Any] = {}
        for key, entry in value.items():
            folded = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if folded in {
                "id",
                "productid",
                "productgid",
                "variantid",
                "variantgid",
                "inventoryitemid",
                "inventoryid",
            }:
                continue
            if "inventory" in folded and "quantit" in folded:
                continue
            cleaned[key] = scrub(entry)
        return cleaned

    clone = scrub(clone)
    clone["sku"] = ""
    clone["barcode"] = ""
    return clone


def _validate_variant_combinations(
    product: dict[str, Any], option_names: list[str]
) -> list[tuple[str, ...]]:
    combos: list[tuple[str, ...]] = []
    declared_values = [
        {_fold(value) for value in _option_values(option)}
        for option in _raw_options(product)
    ]
    for variant in _variants(product):
        _validate_variant_representations(variant, option_names)
        combo = _canonical_combo(variant, option_names)
        if combo is None:
            raise OptionValueEditError(
                "INCOMPLETE_VARIANT_COMBINATION",
                "Every variant must select one value for every option.",
            )
        if any(
            index >= len(declared_values)
            or value not in declared_values[index]
            for index, value in enumerate(combo)
        ):
            raise OptionValueEditError(
                "UNDECLARED_OPTION_VALUE",
                "Every variant option value must be declared by its option.",
            )
        combos.append(combo)
    if len(set(combos)) != len(combos):
        raise OptionValueEditError(
            "DUPLICATE_VARIANT_COMBINATION",
            "Variant option combinations must be unique.",
        )
    if len(combos) > MAX_VARIANTS:
        raise OptionValueEditError(
            "VARIANT_LIMIT_EXCEEDED",
            f"Option edits cannot create more than {MAX_VARIANTS} variants.",
        )
    return combos


def _validate_variant_representations(
    variant: dict[str, Any], option_names: list[str]
) -> None:
    representations: list[tuple[str, ...]] = []
    expected_names = {_fold(name) for name in option_names}
    for key in ("selectedOptions", "selected_options", "optionValues", "option_values"):
        raw = variant.get(key)
        if not isinstance(raw, list):
            continue
        pairs: dict[str, str] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                raise OptionValueEditError(
                    "INCONSISTENT_VARIANT_OPTIONS",
                    "Variant option references must use one consistent representation.",
                )
            if "value" in entry:
                name = entry.get("name") or entry.get("optionName") or entry.get("option_name")
                value = entry.get("value")
            else:
                name = entry.get("optionName") or entry.get("option_name") or entry.get("name")
                value = entry.get("name")
            folded_name = _fold(name)
            if (
                not folded_name
                or folded_name in pairs
                or folded_name not in expected_names
                or not _text(value)
            ):
                raise OptionValueEditError(
                    "INCONSISTENT_VARIANT_OPTIONS",
                    "Variant option references must use one consistent representation.",
                )
            pairs[folded_name] = _fold(value)
        if set(pairs) != expected_names:
            raise OptionValueEditError(
                "INCONSISTENT_VARIANT_OPTIONS",
                "Variant option references must select every option exactly once.",
            )
        representations.append(
            tuple(pairs[_fold(name)] for name in option_names)
        )
    positional_present = any(
        f"option{index}" in variant for index in range(1, 4)
    )
    if positional_present:
        positional = tuple(
            _fold(variant.get(f"option{index + 1}"))
            for index in range(len(option_names))
        )
        unexpected = any(
            _text(variant.get(f"option{index}"))
            for index in range(len(option_names) + 1, 4)
        )
        if not all(positional) or unexpected:
            raise OptionValueEditError(
                "INCONSISTENT_VARIANT_OPTIONS",
                "Positional variant options must match the option dimensions exactly.",
            )
        representations.append(positional)
    if representations and len(set(representations)) != 1:
        raise OptionValueEditError(
            "INCONSISTENT_VARIANT_OPTIONS",
            "Named and positional variant option references do not agree.",
        )


def _mutable_variants(product: dict[str, Any]) -> list[Any]:
    raw = product.get("variants")
    if isinstance(raw, dict):
        nodes = raw.get("nodes")
        return nodes if isinstance(nodes, list) else []
    return raw if isinstance(raw, list) else []


def _sort_variants_by_options(product: dict[str, Any]) -> None:
    option_names = _schema_from_product(product)
    options = _raw_options(product)
    ranks = [
        {_fold(value): rank for rank, value in enumerate(_option_values(option))}
        for option in options
    ]
    variants = _mutable_variants(product)

    def sort_key(variant: dict[str, Any]) -> tuple[int, ...]:
        combo = _canonical_combo(variant, option_names)
        if combo is None:
            return tuple([MAX_VARIANTS] * len(option_names))
        return tuple(
            ranks[index].get(value, MAX_VARIANTS)
            for index, value in enumerate(combo)
        )

    variants.sort(key=sort_key)


def _add_option_value_variants(
    product: dict[str, Any],
    option_index: int,
    option_name: str,
    previous_values: list[str],
    new_value: str,
) -> None:
    option_names = _schema_from_product(product)
    variants = _mutable_variants(product)
    if not variants:
        return
    combos = _validate_variant_combinations(product, option_names)
    other_tuples: list[tuple[str, ...]] = []
    for combo in combos:
        other = combo[:option_index] + combo[option_index + 1 :]
        if other not in other_tuples:
            other_tuples.append(other)
    value_ranks = {_fold(value): index for index, value in enumerate(previous_values)}
    new_rank = len(previous_values)
    additions: list[dict[str, Any]] = []
    for other in other_tuples:
        candidates: list[tuple[int, int, int, dict[str, Any]]] = []
        for order, (variant, combo) in enumerate(zip(variants, combos, strict=True)):
            if combo[:option_index] + combo[option_index + 1 :] != other:
                continue
            rank = value_ranks.get(combo[option_index], MAX_VARIANTS)
            candidates.append(
                (
                    abs(new_rank - rank),
                    0 if rank < new_rank else 1,
                    -rank,
                    variant,
                )
            )
        if not candidates:
            continue
        sibling = min(candidates, key=lambda candidate: candidate[:3])[3]
        clone = _commerce_clone(sibling)
        _set_variant_option_value(
            clone, option_names, option_index, option_name, new_value
        )
        additions.append(clone)
    variants.extend(additions)


def _option_value_impacts(
    live_snapshot: dict[str, Any] | None,
    proposed: dict[str, Any],
    option_name: str,
) -> dict[str, dict[str, int]]:
    option_names = _schema_from_product(proposed)
    target_index = next(
        (
            index
            for index, name in enumerate(option_names)
            if _fold(name) == _fold(option_name)
        ),
        -1,
    )
    if target_index < 0:
        return {}
    live = live_snapshot or {}
    live_names = _schema_from_product(live)
    live_ids = {
        str(variant.get("id"))
        for variant in _variants(live)
        if variant.get("id")
    }
    live_combos = {
        combo
        for variant in _variants(live)
        if (combo := _canonical_combo(variant, live_names or option_names)) is not None
    }
    impacts: dict[str, dict[str, int]] = {}
    for variant in _variants(proposed):
        combo = _canonical_combo(variant, option_names)
        if combo is None:
            continue
        value_map = _variant_value_map(variant, option_names)
        value = value_map.get(_fold(option_name))
        if value is None:
            continue
        impact = impacts.setdefault(
            value, {"total": 0, "live": 0, "proposed_only": 0}
        )
        impact["total"] += 1
        is_live = bool(
            variant.get("id") and str(variant.get("id")) in live_ids
        ) or combo in live_combos
        if is_live:
            impact["live"] += 1
        else:
            impact["proposed_only"] += 1
    for value in _option_values(_raw_options(proposed)[target_index]):
        impacts.setdefault(value, {"total": 0, "live": 0, "proposed_only": 0})
    return impacts


def apply_option_value_operation(
    product: dict[str, Any],
    *,
    option_name: str,
    action: str,
    from_value: Any = None,
    to_value: Any = None,
    value: Any = None,
    values: Any = None,
) -> dict[str, Any]:
    """Apply one option-value operation to a product-shaped proposal."""
    result = deepcopy(product)
    option_names_before = _schema_from_product(result)
    option_index, option = _resolve_option(result, option_name)
    current_values = _require_raw_option_values(option)
    variants = _mutable_variants(result)
    if variants:
        _validate_variant_combinations(result, option_names_before)

    renamed_from: str | None = None
    renamed_to: str | None = None
    next_values = list(current_values)
    if action == "rename":
        source = _text(from_value)
        target = _text(to_value)
        if not source or not target:
            raise OptionValueEditError(
                "BLANK_OPTION_VALUE", "Rename values cannot be blank."
            )
        matches = [
            current for current in current_values if _fold(current) == _fold(source)
        ]
        if len(matches) != 1:
            raise OptionValueEditError(
                "OPTION_VALUE_MISSING",
                "The option value to rename is no longer available.",
            )
        renamed_from, renamed_to = matches[0], target
        if any(
            _fold(current) == _fold(target) and current != renamed_from
            for current in current_values
        ):
            raise OptionValueEditError(
                "DUPLICATE_OPTION_VALUE",
                "Option values must be unique, ignoring capitalization.",
            )
        next_values = [
            target if current == renamed_from else current
            for current in current_values
        ]
        for variant in variants:
            value_map = _variant_value_map(variant, option_names_before)
            if _fold(value_map.get(_fold(option_name))) == _fold(renamed_from):
                _set_variant_option_value(
                    variant, option_names_before, option_index, option_name, target
                )
    elif action == "add":
        target = _text(value)
        if not target:
            raise OptionValueEditError(
                "BLANK_OPTION_VALUE", "The new option value cannot be blank."
            )
        if any(_fold(current) == _fold(target) for current in current_values):
            raise OptionValueEditError(
                "DUPLICATE_OPTION_VALUE",
                "Option values must be unique, ignoring capitalization.",
            )
        next_values.append(target)
        _add_option_value_variants(
            result, option_index, option_name, current_values, target
        )
    elif action == "remove":
        target = _text(value)
        matches = [
            current for current in current_values if _fold(current) == _fold(target)
        ]
        if len(matches) != 1:
            raise OptionValueEditError(
                "OPTION_VALUE_MISSING",
                "The option value to remove is no longer available.",
            )
        if len(current_values) == 1:
            raise OptionValueEditError(
                "FINAL_OPTION_VALUE",
                "An option must keep at least one value.",
            )
        removed = matches[0]
        next_values = [
            current for current in current_values if current != removed
        ]
        variants[:] = [
            variant
            for variant in variants
            if _fold(
                _variant_value_map(variant, option_names_before).get(
                    _fold(option_name)
                )
            )
            != _fold(removed)
        ]
    elif action == "reorder":
        if (
            not isinstance(values, list)
            or any(not isinstance(entry, str) for entry in values)
        ):
            raise OptionValueEditError(
                "INVALID_OPTION_REORDER",
                "Reorder must include every option value exactly once.",
            )
        _require_unique_option_values(values)
        if {_fold(entry) for entry in values} != {
            _fold(entry) for entry in current_values
        }:
            raise OptionValueEditError(
                "INVALID_OPTION_REORDER",
                "Reorder must include every option value exactly once.",
            )
        existing = {_fold(entry): entry for entry in current_values}
        next_values = [existing[_fold(entry)] for entry in values]
    else:
        raise OptionValueEditError(
            "INVALID_OPTION_ACTION", "This option action is not supported."
        )

    _require_unique_option_values(next_values)
    _replace_option_values(
        option,
        next_values,
        renamed_from=renamed_from,
        renamed_to=renamed_to,
    )
    if _schema_from_product(result) != option_names_before:
        raise OptionValueEditError(
            "OPTION_DIMENSIONS_CHANGED",
            "Option-value edits cannot change option dimensions.",
        )
    if variants:
        _validate_variant_combinations(result, option_names_before)
        _sort_variants_by_options(result)
    return result


def build_review_plan_item(
    *,
    item_id: str,
    currency_code: str,
    source_index: int,
    source_product: dict[str, Any],
    live_snapshot: dict[str, Any] | None,
    proposed: dict[str, Any],
    operation: str,
    blockers: list[dict[str, Any]] | None = None,
    unapplied: list[dict[str, Any]] | None = None,
    prior_item: dict[str, Any] | None = None,
    subject_renames: dict[str, str] | None = None,
    variant_identity_rename: tuple[int, str, str] | None = None,
) -> dict[str, Any]:
    """Purely assemble one reusable v2 review item from resolved inputs."""
    live = deepcopy(live_snapshot) if isinstance(live_snapshot, dict) else None
    imported = deepcopy(source_product)
    proposed_copy = deepcopy(proposed)
    blockers_copy = deepcopy(blockers or [])
    unapplied_copy = deepcopy(unapplied or [])
    changes = applicable_changes(live, proposed_copy)
    exact_review = review_changes(
        live,
        imported,
        proposed_copy,
        blockers=blockers_copy,
        unapplied=unapplied_copy,
    )
    previous = prior_item if isinstance(prior_item, dict) else {}
    prior_rows = [
        row
        for row in previous.get("review_changes", [])
        if isinstance(row, dict)
    ]
    represented_options = {
        (_fold(row.get("subject")), str(row.get("field") or ""))
        for row in exact_review
        if row.get("scope") == "option"
    }
    proposed_options = {
        _fold(option.get("name")): option
        for option in _raw_options(proposed_copy)
    }
    live_options = {
        _fold(option.get("name")): option for option in _raw_options(live or {})
    }
    for prior_row in prior_rows:
        option_key = (
            _fold(prior_row.get("subject")),
            str(prior_row.get("field") or ""),
        )
        if (
            prior_row.get("scope") != "option"
            or prior_row.get("field") != "values"
            or option_key in represented_options
            or option_key[0] not in proposed_options
        ):
            continue
        anchor = deepcopy(prior_row)
        anchor["current"] = _option_values(live_options.get(option_key[0], {}))
        anchor["proposed"] = _option_values(proposed_options[option_key[0]])
        anchor["disposition"] = "applicable"
        anchor["change_kind"] = "unchanged"
        anchor["included"] = bool(prior_row.get("included", True))
        exact_review.append(anchor)
        represented_options.add(option_key)

    previous_rows = {
        str(row.get("change_id")): row
        for row in prior_rows
        if row.get("change_id")
    }
    def semantic_subject(row: dict[str, Any]) -> str:
        identity = row.get("subject_identity")
        if (
            row.get("scope") in {"variant", "inventory"}
            and isinstance(identity, dict)
            and identity.get("kind") == "variant_options"
            and isinstance(identity.get("values"), list)
        ):
            return json.dumps(
                {
                    "option_names": identity.get("option_names"),
                    "values": identity["values"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        return str(row.get("subject") or "")

    previous_semantic = {
        (
            str(row.get("scope") or ""),
            semantic_subject(row),
            str(row.get("field") or ""),
        ): row
        for row in prior_rows
    }
    renamed_subjects = subject_renames or {}
    for row in exact_review:
        prior_row = previous_rows.get(str(row.get("change_id") or ""))
        if not isinstance(prior_row, dict):
            prior_subject = semantic_subject(row)
            identity = row.get("subject_identity")
            if (
                variant_identity_rename is not None
                and row.get("scope") in {"variant", "inventory"}
                and isinstance(identity, dict)
                and isinstance(identity.get("values"), list)
            ):
                option_index, renamed_value, prior_value = variant_identity_rename
                prior_identity = deepcopy(identity)
                identity_values = prior_identity["values"]
                if (
                    0 <= option_index < len(identity_values)
                    and _fold(identity_values[option_index]) == _fold(renamed_value)
                ):
                    identity_values[option_index] = _fold(prior_value)
                    prior_subject = json.dumps(
                        {
                            "option_names": prior_identity.get("option_names"),
                            "values": identity_values,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
            prior_subject = renamed_subjects.get(
                str(row.get("subject") or ""), prior_subject
            )
            prior_row = previous_semantic.get(
                (
                    str(row.get("scope") or ""),
                    prior_subject,
                    str(row.get("field") or ""),
                )
            )
        if isinstance(prior_row, dict):
            row["change_id"] = str(prior_row.get("change_id") or row["change_id"])
            row["included"] = bool(prior_row.get("included"))
            row["origin"] = prior_row.get("origin", row.get("origin", "unknown"))
            if isinstance(prior_row.get("source_ref"), dict):
                row["source_ref"] = deepcopy(prior_row["source_ref"])
            else:
                row.pop("source_ref", None)
            if isinstance(prior_row.get("source_refs"), list):
                row["source_refs"] = deepcopy(prior_row["source_refs"])
            else:
                row.pop("source_refs", None)
            for key in (
                "original_proposed",
                "original_origin",
                "original_source_ref",
                "original_source_refs",
            ):
                if key in prior_row:
                    row[key] = deepcopy(prior_row[key])
        else:
            if previous:
                row["origin"] = "merchant_edited"
                row.pop("source_ref", None)
            if row.get("required"):
                row["included"] = row.get("disposition") == "applicable"
            row["original_proposed"] = deepcopy(row.get("proposed"))
            row["original_origin"] = row.get("origin", "unknown")
            row["original_source_ref"] = (
                deepcopy(row.get("source_ref"))
                if isinstance(row.get("source_ref"), dict)
                else None
            )
            row["original_source_refs"] = deepcopy(row.get("source_refs") or [])
        if row.get("scope") == "option" and row.get("field") == "values":
            row["option_value_impacts"] = _option_value_impacts(
                live, proposed_copy, str(row.get("subject") or "")
            )
    resolved_operation = str(operation)
    if blockers_copy:
        resolved_operation = "blocked"
    elif live and not changes:
        resolved_operation = "unchanged"
    selectable = resolved_operation in {"create", "update"} and bool(changes)
    item = {
        "item_id": str(item_id),
        "currency_code": str(currency_code),
        "source_index": int(source_index),
        "source_evidence": deepcopy(imported.get("source_refs") or []),
        "live_snapshot": live,
        "proposed": proposed_copy,
        "operation": resolved_operation,
        "resolved_product_id": live.get("id") if live else None,
        "blockers": blockers_copy,
        "unapplied": unapplied_copy,
        "applicable_changes": changes,
        "review_changes": exact_review,
        "selectable": selectable,
        "selected": bool(selectable and previous.get("selected", True)),
        "remedy": None,
        "execution": None,
    }
    return normalize_review_plan_rows(
        {"review_contract_version": 2, "items": [item]}
    )["items"][0]
