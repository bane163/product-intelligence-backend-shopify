from __future__ import annotations

import uuid
from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort
from application.use_cases.intelligence_generate_suggestions import execute as generate


async def execute(*, supabase: SupabaseNamespacedPort, suggestion_id: str, field: str,
                  product: dict[str, Any], shop_domain: str,
                  trace_event: Any | None = None) -> dict[str, Any]:
    source = supabase.intelligence.get_product_intelligence_suggestion(
        suggestion_id, shop_domain=shop_domain
    )
    if not source:
        raise LookupError("Suggestion not found")
    if source.get("status") != "pending":
        raise ValueError("Suggestion is no longer actionable")
    patch = source.get("patch_payload")
    if not isinstance(patch, dict) or field not in patch:
        raise ValueError("Requested field does not belong to this suggestion")
    source_product_id = str(source.get("product_id") or "")
    if not source_product_id or str(product.get("id") or "") != source_product_id:
        raise ValueError("Product does not match the suggestion target")

    # Generate in memory first. Nothing is superseded until a matching field exists.
    contextual_product = dict(product)
    contextual_product["_regeneration_context"] = {
        "category": source.get("category"), "message": source.get("message"),
        "details": source.get("details"), "proposal": patch.get(field), "field": field,
    }
    candidates = await generate(supabase=supabase, products=[contextual_product],
                                shop_domain=shop_domain, trace_event=trace_event)
    candidate = next((item for item in candidates
                      if isinstance(item.get("patch_payload"), dict)
                      and field in item["patch_payload"]), None)
    if not candidate:
        raise ValueError("No regenerated candidate was produced for the requested field")

    root = str(source.get("root_suggestion_id") or suggestion_id)
    common = {key: source.get(key) for key in (
        "audit_id", "finding_id", "product_index", "product_title", "product_id",
        "category", "severity", "message", "details",
    )}
    created_ids: list[str] = []
    try:
        replacement = supabase.intelligence.create_product_intelligence_suggestion(
            suggestion={**common, "suggestion_id": str(uuid.uuid4()), "status": "pending",
                        "patch_payload": {field: candidate["patch_payload"][field]},
                        "parent_suggestion_id": suggestion_id, "root_suggestion_id": root},
            shop_domain=shop_domain)
        if not replacement:
            raise RuntimeError("Failed to persist regenerated suggestion")
        created_ids.append(str(replacement["suggestion_id"]))
        siblings = {key: value for key, value in patch.items() if key != field}
        carry = None
        if siblings:
            carry = supabase.intelligence.create_product_intelligence_suggestion(
                suggestion={**common, "suggestion_id": str(uuid.uuid4()), "status": "pending",
                            "patch_payload": siblings, "parent_suggestion_id": suggestion_id,
                            "root_suggestion_id": root}, shop_domain=shop_domain)
            if not carry:
                raise RuntimeError("Failed to persist sibling suggestion")
            created_ids.append(str(carry["suggestion_id"]))
        superseded = supabase.intelligence.mark_product_intelligence_suggestion_superseded(
            suggestion_id=suggestion_id, shop_domain=shop_domain)
        if not superseded:
            raise RuntimeError("Failed to supersede source suggestion")
    except Exception:
        for child_id in created_ids:
            supabase.intelligence.mark_product_intelligence_suggestion_superseded(
                suggestion_id=child_id, shop_domain=shop_domain)
        raise
    return {"suggestion": replacement, "carry_forward_suggestion": carry,
            "source_suggestion": superseded}
