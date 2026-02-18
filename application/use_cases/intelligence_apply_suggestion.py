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

    submitted_id = audit.get("submitted_id")
    if not isinstance(submitted_id, str) or not submitted_id:
        raise ValueError("Suggestion apply currently supports submitted-document audits only")

    submitted = supabase.get_submitted_document(submitted_id)
    if not submitted:
        raise LookupError("Submitted document not found")

    products = submitted.get("products")
    if not isinstance(products, list):
        raise ValueError("Submitted document products are invalid")

    product_index = int(suggestion.get("product_index") or 0)
    if product_index < 0 or product_index >= len(products):
        raise ValueError("Suggestion product index is out of bounds")

    current_product = products[product_index]
    if not isinstance(current_product, dict):
        raise ValueError("Submitted product is invalid")

    updated_product = _merge_product_patch(current_product, patch_payload)
    products[product_index] = updated_product

    save_result = supabase.save_submitted_document(
        submitted_id=submitted_id,
        run_id=submitted.get("run_id") if isinstance(submitted.get("run_id"), str) else None,
        draft_id=submitted.get("draft_id") if isinstance(submitted.get("draft_id"), str) else None,
        name=str(submitted.get("name") or "Submitted document"),
        import_mode=str(submitted.get("import_mode") or "auto"),
        product_count=len(products),
        products=[item for item in products if isinstance(item, dict)],
    )

    shopify_updated = False
    gid = await _resolve_product_gid(shopify, updated_product)
    if gid:
        await shopify.update_product_from_input({**updated_product, "id": gid})
        shopify_updated = True

    applied = supabase.mark_product_intelligence_suggestion_applied(suggestion_id=suggestion_id)
    if not applied:
        raise RuntimeError("Failed to mark suggestion as applied")

    return {
        "status": "applied",
        "suggestion": applied,
        "shopify_updated": shopify_updated,
        "submitted_document": save_result,
    }
