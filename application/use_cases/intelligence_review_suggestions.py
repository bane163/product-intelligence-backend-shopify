from __future__ import annotations

from typing import Any

from application.ports.shopify_port import ShopifyPort
from application.ports.supabase_port import SupabaseNamespacedPort
from application.use_cases.intelligence_apply_suggestion import (
    _normalize_metafields_payload,
    _normalize_shopify_product,
    _resolve_product_gid,
    _resolve_suggestion_product,
    execute as apply_suggestion,
)
from application.domain.variant_matrix import validate_variant_matrix

ROUTINE_FIELDS = {
    "title", "body_html", "description", "vendor", "product_type",
    "product_category", "seo_title", "seo_description", "tags",
}
SUPPORTED_FIELDS = ROUTINE_FIELDS | {"metafields", "variant_operations"}


def classify_field(suggestion: dict[str, Any], field: str, *, changed: bool) -> dict[str, Any]:
    reasons: list[str] = []
    supported = field in SUPPORTED_FIELDS
    reversible = field != "variant_operations"
    details = suggestion.get("details") if isinstance(suggestion.get("details"), dict) else {}
    elevated = field not in ROUTINE_FIELDS or changed or bool(details.get("requires_review"))
    if not supported:
        reasons.append("This historical field is unsupported; run a new audit.")
    if field in {"metafields", "variant_operations"}:
        reasons.append("This change can create or modify structured Shopify resources.")
    if changed:
        reasons.append("The Shopify value changed since this audit.")
    if details.get("requires_review"):
        reasons.append("The audit explicitly requires merchant review.")
    if not reversible:
        reasons.append("Replacing the product option and variant matrix is not automatically reversible.")
    return {
        "risk": "elevated" if elevated else "routine",
        "risk_reasons": reasons,
        "supported": supported,
        "reversible": reversible,
        "selected_by_default": supported and reversible and not elevated,
    }


async def preview(
    *, supabase: SupabaseNamespacedPort, shopify: ShopifyPort,
    reviews: list[dict[str, Any]], shop_domain: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for review in reviews:
        suggestion_id = str(review.get("suggestion_id") or "").strip()
        suggestion = supabase.intelligence.get_product_intelligence_suggestion(
            suggestion_id, shop_domain=shop_domain
        )
        if not suggestion:
            results.append({"suggestion_id": suggestion_id, "status": "not_found", "fields": []})
            continue
        status = str(suggestion.get("status") or "pending")
        patch = suggestion.get("patch_payload") if isinstance(suggestion.get("patch_payload"), dict) else {}
        edited = review.get("patch_payload") if isinstance(review.get("patch_payload"), dict) else {}
        audit = supabase.intelligence.get_product_intelligence_audit(
            str(suggestion.get("audit_id") or ""), shop_domain=shop_domain
        )
        if not audit:
            results.append({"suggestion_id": suggestion_id, "status": "unsupported", "fields": []})
            continue
        audited_product = _resolve_suggestion_product(suggestion, audit)
        gid = str(suggestion.get("product_id") or "").strip() or await _resolve_product_gid(shopify, audited_product)
        live = _normalize_shopify_product(await shopify.get_product(gid)) if gid else {}
        fields: list[dict[str, Any]] = []
        for field, ai_value in patch.items():
            normalized_field = "product_type" if field == "product_category" else field
            audited_value = audited_product.get(normalized_field)
            current_value = live.get(normalized_field)
            if field == "metafields":
                requested = _normalize_metafields_payload(ai_value)
                identifiers = [{"namespace": x["namespace"], "key": x["key"]} for x in requested]
                current_value = await shopify.get_product_metafields(gid, identifiers) if gid else []
                audited_value = None
            changed = audited_value != current_value and audited_value is not None
            policy = classify_field(suggestion, field, changed=changed)
            validation_errors: list[str] = []
            summary = None
            if field == "variant_operations":
                proposed = edited.get(field, ai_value)
                normalized_matrix, validation_errors = validate_variant_matrix(proposed)
                option_count = len(normalized_matrix.get("create_options", []))
                variant_count = len(normalized_matrix.get("create_variants", []))
                summary = {"option_count": option_count, "variant_count": variant_count, "initial_inventory": 0}
                current_value = {"options": live.get("options", []), "variants": live.get("variants", [])}
            fields.append({
                "field": field,
                "audited_value": audited_value,
                "current_value": current_value,
                "proposed_value": edited.get(field, ai_value),
                "changed_since_audit": changed,
                "actionable": policy["supported"] and not validation_errors,
                "validation_errors": validation_errors,
                "summary": summary,
                **policy,
            })
        results.append({
            "suggestion_id": suggestion_id, "product_id": gid, "status": status,
            "message": suggestion.get("message"), "details": suggestion.get("details") or {},
            "fields": fields,
        })
    return {"reviews": results}


async def apply_reviewed(
    *, supabase: SupabaseNamespacedPort, shopify: ShopifyPort,
    reviews: list[dict[str, Any]], shop_domain: str, safe_only: bool = False,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for review in reviews:
        suggestion_id = str(review.get("suggestion_id") or "").strip()
        patch = review.get("patch_payload") if isinstance(review.get("patch_payload"), dict) else {}
        expected = review.get("expected_current") if isinstance(review.get("expected_current"), dict) else {}
        check = await preview(
            supabase=supabase, shopify=shopify,
            reviews=[{"suggestion_id": suggestion_id, "patch_payload": patch}],
            shop_domain=shop_domain,
        )
        item = check["reviews"][0]
        conflicts = [
            {"field": field["field"], "expected": expected.get(field["field"]), "current": field["current_value"]}
            for field in item.get("fields", [])
            if field["field"] in patch and field["field"] in expected
            and expected[field["field"]] != field["current_value"]
        ]
        if conflicts:
            results.append({"suggestion_id": suggestion_id, "status": "conflict", "conflicts": conflicts})
            continue
        allowed = {
            field["field"] for field in item.get("fields", [])
            if field.get("supported") and field.get("actionable", True) and (not safe_only or field.get("risk") == "routine")
        }
        effective = {key: value for key, value in patch.items() if key in allowed}
        if not effective:
            results.append({"suggestion_id": suggestion_id, "status": "skipped", "error": "No eligible fields selected"})
            continue
        try:
            applied = await apply_suggestion(
                supabase=supabase, shopify=shopify, suggestion_id=suggestion_id,
                patch_payload=effective, shop_domain=shop_domain,
            )
            results.append({"suggestion_id": suggestion_id, **applied})
        except Exception as exc:
            results.append({"suggestion_id": suggestion_id, "status": "failed", "error": str(exc)})
    return {
        "results": results,
        "applied_count": sum(1 for x in results if x.get("status") == "applied"),
        "failed_count": sum(1 for x in results if x.get("status") in {"failed", "conflict"}),
    }
