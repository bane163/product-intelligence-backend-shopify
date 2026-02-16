"""
Product-related domain helpers.
"""
from typing import Any


def extract_first_sku(item: dict[str, Any]) -> str | None:
    sku = item.get("sku")
    if isinstance(sku, str) and sku.strip():
        return sku.strip()
    variants = item.get("variants")
    if isinstance(variants, list):
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_sku = variant.get("sku")
            if isinstance(variant_sku, str) and variant_sku.strip():
                return variant_sku.strip()
    return None
