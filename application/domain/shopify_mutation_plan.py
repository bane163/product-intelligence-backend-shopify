"""Pure validation, merge, and diff helpers for reviewed Shopify mutations."""

from __future__ import annotations

import re
from typing import Any

from application.domain.shopify_product_normalization import normalize_core_product

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
}
VALID_STATUSES = {"ACTIVE", "ARCHIVED", "DRAFT", "UNLISTED"}
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
    "cost": ("cost", "unit_cost"),
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
    return _text(variant.get("title")) or _text(variant.get("id")) or "Variant"


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
) -> dict[str, Any]:
    row = {
        "scope": scope,
        "subject": subject,
        "field": field,
        "current": current,
        "proposed": proposed,
        "disposition": disposition,
        "change_kind": change_kind,
        "value_type": value_type
        or (
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
        ),
    }
    if reason_codes:
        row["reason_codes"] = reason_codes
    return row


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
                    )
                )

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
            )
        )
    return rows
