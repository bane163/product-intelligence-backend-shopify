from __future__ import annotations

from decimal import Decimal, InvalidOperation
from itertools import product
import re
from typing import Any

CATALOG_HEALTH_VARIANT_LIMIT = 100
SHOPIFY_VARIANT_LIMIT = 2048


def _text(value: Any) -> str:
    return str(value or "").strip()


def _key(option_values: list[dict[str, Any]], option_names: list[str]) -> tuple[str, ...]:
    by_name = {_text(x.get("option_name")): _text(x.get("name")) for x in option_values if isinstance(x, dict)}
    return tuple(by_name.get(name, "") for name in option_names)


def validate_variant_matrix(raw: Any, *, limit: int = CATALOG_HEALTH_VARIANT_LIMIT) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["Variant operations must be an object."]
    options_raw = raw.get("create_options")
    variants_raw = raw.get("create_variants")
    if not isinstance(options_raw, list) or not options_raw:
        errors.append("At least one option is required.")
        options_raw = []
    if not isinstance(variants_raw, list) or not variants_raw:
        errors.append("A complete variant matrix is required.")
        variants_raw = []

    options: list[dict[str, Any]] = []
    names: list[str] = []
    for index, item in enumerate(options_raw):
        if not isinstance(item, dict):
            errors.append(f"Option {index + 1} is invalid.")
            continue
        name = _text(item.get("name"))
        values_raw = item.get("values")
        values = [_text(x) for x in values_raw] if isinstance(values_raw, list) else []
        if not name: errors.append(f"Option {index + 1} needs a name.")
        if not values or any(not x for x in values): errors.append(f"Option {name or index + 1} needs non-empty values.")
        if len(set(x.casefold() for x in values)) != len(values): errors.append(f"Option {name or index + 1} has duplicate values.")
        names.append(name)
        options.append({"name": name, "values": values})
    if not 1 <= len(options) <= 3: errors.append("Variant matrices require 1 to 3 options.")
    if len(set(x.casefold() for x in names)) != len(names): errors.append("Option names must be unique.")

    expected = 1
    for option in options: expected *= len(option["values"])
    if expected > limit: errors.append(f"Variant matrix has {expected} combinations; the limit is {limit}.")
    if expected > SHOPIFY_VARIANT_LIMIT: errors.append(f"Variant matrix exceeds Shopify's {SHOPIFY_VARIANT_LIMIT}-variant limit.")
    if len(variants_raw) != expected: errors.append(f"Expected {expected} variants but found {len(variants_raw)}.")

    allowed = {x["name"]: set(x["values"]) for x in options}
    variants: list[dict[str, Any]] = []
    combinations: list[tuple[str, ...]] = []
    skus: list[str] = []
    for index, item in enumerate(variants_raw):
        if not isinstance(item, dict): errors.append(f"Variant {index + 1} is invalid."); continue
        option_values = item.get("option_values") if isinstance(item.get("option_values"), list) else []
        combo = _key(option_values, names)
        if len(option_values) != len(names) or any(not x for x in combo): errors.append(f"Variant {index + 1} must select every option exactly once.")
        elif any(combo[i] not in allowed[names[i]] for i in range(len(names))): errors.append(f"Variant {index + 1} selects an unknown option value.")
        combinations.append(combo)
        sku = _text(item.get("sku")); skus.append(sku)
        if not sku: errors.append(f"Variant {index + 1} needs a SKU.")
        price = _text(item.get("price"))
        try:
            if Decimal(price) < 0: raise InvalidOperation
        except (InvalidOperation, ValueError): errors.append(f"Variant {index + 1} needs a valid non-negative price.")
        if "inventory_quantity" in item: errors.append(f"Variant {index + 1} cannot override initial inventory.")
        variants.append({"option_values": [{"option_name": names[i], "name": combo[i]} for i in range(len(names))], "sku": sku, "price": price})
    if len(set(combinations)) != len(combinations): errors.append("Variant combinations must be unique.")
    if len(set(x.casefold() for x in skus if x)) != len([x for x in skus if x]): errors.append("Variant SKUs must be unique.")
    expected_combos = set(product(*(x["values"] for x in options))) if options else set()
    if set(combinations) != expected_combos: errors.append("Variants must contain the complete Cartesian option matrix.")
    return {"create_options": options, "create_variants": variants, "defaults": {"initial_inventory": 0, "requires_review": True}}, list(dict.fromkeys(errors))


def build_variant_matrix(*, dimensions: list[dict[str, Any]], sku_prefix: str, price: Any) -> dict[str, Any]:
    options = [{"name": _text(x.get("dimension")), "values": [_text(v) for v in x.get("canonical_values", [])]} for x in dimensions]
    variants = []
    used: set[str] = set()
    for combo in product(*(x["values"] for x in options)):
        base = "-".join([sku_prefix, *[re.sub(r"[^A-Za-z0-9]+", "-", x).strip("-").upper() or "OPT" for x in combo]])
        sku = base; suffix = 2
        while sku.casefold() in used: sku = f"{base}-{suffix}"; suffix += 1
        used.add(sku.casefold())
        variants.append({"option_values": [{"option_name": options[i]["name"], "name": combo[i]} for i in range(len(options))], "sku": sku, "price": _text(price)})
    matrix, errors = validate_variant_matrix({"create_options": options, "create_variants": variants})
    if errors: raise ValueError("; ".join(errors))
    return matrix
