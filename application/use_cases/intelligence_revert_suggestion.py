from __future__ import annotations

from typing import Any

from application.ports.shopify_port import ShopifyPort
from application.ports.supabase_port import SupabasePort
from application.use_cases.intelligence_apply_suggestion import (
    REVERT_MODE_CLEAR,
    REVERT_MODE_RESTORE,
    REVERT_MODE_UNSUPPORTED_CLEAR,
    REVERT_MODES_KEY,
    REVERT_REASON_KEY,
    REVERT_REVERSIBLE_KEY,
    _is_unset_value,
    _supports_field_clear,
    _normalize_revert_field,
    _resolve_product_gid,
    _resolve_suggestion_product,
)


def _clear_value_for_field(field: str) -> Any:
    normalized = _normalize_revert_field(field)
    clear_values: dict[str, Any] = {
        "vendor": "",
        "product_type": "",
        "body_html": "",
        "seo_title": "",
        "seo_description": "",
        "tags": [],
    }
    if normalized not in clear_values:
        raise ValueError(f"Auto-revert clear strategy is not supported for field '{field}'")
    return clear_values[normalized]


async def execute(
    *,
    supabase: SupabasePort,
    shopify: ShopifyPort,
    suggestion_id: str,
    shop_domain: str | None = None,
) -> dict[str, Any]:
    suggestion = supabase.get_product_intelligence_suggestion(
        suggestion_id,
        shop_domain=shop_domain,
    )
    if not suggestion:
        raise LookupError("Suggestion not found")

    if str(suggestion.get("status") or "") != "applied":
        return {
            "status": str(suggestion.get("status") or "pending"),
            "suggestion": suggestion,
            "shopify_updated": False,
        }

    previous_payload = suggestion.get("previous_payload")
    if not isinstance(previous_payload, dict) or not previous_payload:
        raise ValueError("Suggestion has no previous payload to revert")
    if previous_payload.get(REVERT_REVERSIBLE_KEY) is False:
        reason = previous_payload.get(REVERT_REASON_KEY)
        raise ValueError(
            str(reason)
            if isinstance(reason, str) and reason.strip()
            else "This fix is not reversible automatically"
        )

    audit_id = suggestion.get("audit_id")
    if not isinstance(audit_id, str) or not audit_id:
        raise ValueError("Suggestion is missing audit reference")

    audit = supabase.get_product_intelligence_audit(audit_id, shop_domain=shop_domain)
    if not audit:
        raise LookupError("Audit not found for suggestion")

    current_product = _resolve_suggestion_product(suggestion=suggestion, audit=audit)
    gid = await _resolve_product_gid(shopify, current_product)
    if not gid:
        raise ValueError("Unable to resolve Shopify product ID for suggestion target")

    patch_payload = suggestion.get("patch_payload")
    if not isinstance(patch_payload, dict) or not patch_payload:
        raise ValueError("Suggestion has no patch payload to revert")
    restore_payload: dict[str, Any] = {}
    revert_modes = previous_payload.get(REVERT_MODES_KEY)
    for field in patch_payload.keys():
        update_field = _normalize_revert_field(field)
        mode = revert_modes.get(field) if isinstance(revert_modes, dict) else None
        if mode == REVERT_MODE_CLEAR:
            restore_payload[update_field] = _clear_value_for_field(field)
            continue
        if mode == REVERT_MODE_UNSUPPORTED_CLEAR:
            raise ValueError(
                f"Auto-revert clear strategy is not supported for field '{field}'"
            )
        if mode == REVERT_MODE_RESTORE:
            restore_payload[update_field] = previous_payload.get(field)
            continue
        if field not in previous_payload:
            continue
        original_value = previous_payload.get(field)
        if _is_unset_value(original_value):
            if not _supports_field_clear(field):
                raise ValueError(
                    f"Auto-revert clear strategy is not supported for field '{field}'"
                )
            restore_payload[update_field] = _clear_value_for_field(field)
        else:
            restore_payload[update_field] = original_value
    if not restore_payload:
        raise ValueError("Suggestion has no reversible fields")

    await shopify.update_product_from_input({"id": gid, **restore_payload})

    reverted = supabase.mark_product_intelligence_suggestion_pending(
        suggestion_id=suggestion_id,
        shop_domain=shop_domain,
    )
    if not reverted:
        raise RuntimeError("Failed to mark suggestion as pending")

    return {
        "status": "pending",
        "suggestion": reverted,
        "shopify_updated": True,
        "target_product_id": gid,
    }
