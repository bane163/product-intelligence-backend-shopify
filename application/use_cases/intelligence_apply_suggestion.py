from __future__ import annotations

from typing import Any

from application.domain.product import extract_first_sku
from application.ports.shopify_port import ShopifyPort
from application.ports.supabase_port import SupabasePort


def _merge_product_patch(product: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(product)
    for key, value in patch.items():
        merged[key] = value
    return merged


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _extract_audited_products(audit: dict[str, Any]) -> list[dict[str, Any]]:
    totals = audit.get("totals")
    if not isinstance(totals, dict):
        return []
    raw = totals.get("audited_products")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _resolve_suggestion_product(
    suggestion: dict[str, Any], audit: dict[str, Any]
) -> dict[str, Any]:
    products = _extract_audited_products(audit)
    if not products:
        raise ValueError("Audit does not include audited product metadata")

    try:
        product_index = int(suggestion.get("product_index") or 0)
    except (TypeError, ValueError):
        product_index = 0
    if 0 <= product_index < len(products):
        return dict(products[product_index])

    target_title = _normalize_text(suggestion.get("product_title"))
    if target_title:
        for item in products:
            if _normalize_text(item.get("title")) == target_title:
                return dict(item)

    raise ValueError("Unable to resolve suggestion target product from audit metadata")


async def _resolve_product_gid(shopify: ShopifyPort, product: dict[str, Any]) -> str | None:
    gid = product.get("id") or product.get("shopify_gid")
    if isinstance(gid, str) and gid:
        return gid

    handle = product.get("handle")
    if isinstance(handle, str) and handle:
        resolved = await shopify.find_product_id_by_handle(handle)
        if resolved:
            return resolved

    sku = extract_first_sku(product)
    if sku:
        resolved = await shopify.find_product_id_by_sku(sku)
        if resolved:
            return resolved

    return None


async def execute(
    *,
    supabase: SupabasePort,
    shopify: ShopifyPort,
    suggestion_id: str,
) -> dict[str, Any]:
    suggestion = supabase.get_product_intelligence_suggestion(suggestion_id)
    if not suggestion:
        raise LookupError("Suggestion not found")

    if str(suggestion.get("status") or "") == "applied":
        return {"status": "applied", "suggestion": suggestion, "shopify_updated": False}

    patch_payload = suggestion.get("patch_payload")
    if not isinstance(patch_payload, dict) or not patch_payload:
        raise ValueError("Suggestion has no actionable patch payload")

    audit_id = suggestion.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id:
        raise ValueError("Suggestion is missing audit reference")

    audit = supabase.get_product_intelligence_audit(audit_id)
    if not audit:
        raise LookupError("Audit not found for suggestion")

    current_product = _resolve_suggestion_product(suggestion=suggestion, audit=audit)
    updated_product = _merge_product_patch(current_product, patch_payload)
    gid = await _resolve_product_gid(shopify, updated_product)
    if not gid:
        raise ValueError("Unable to resolve Shopify product ID for suggestion target")
    await shopify.update_product_from_input({**updated_product, "id": gid})

    applied = supabase.mark_product_intelligence_suggestion_applied(suggestion_id=suggestion_id)
    if not applied:
        raise RuntimeError("Failed to mark suggestion as applied")

    return {
        "status": "applied",
        "suggestion": applied,
        "shopify_updated": True,
        "target_product_id": gid,
    }
