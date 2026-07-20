from __future__ import annotations

from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort


def _health(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {
            "state": "not_audited", "score": None, "component_scores": {},
            "findings_count": 0, "audit_id": None, "run_id": None,
            "last_audited_at": None, "requires_reaudit": True,
        }
    score = int(result.get("score") or 0)
    return {
        "state": "healthy" if score >= 80 else "warning" if score >= 60 else "critical",
        "score": score,
        "component_scores": result.get("component_scores") or {},
        "findings_count": int(result.get("findings_count") or 0),
        "audit_id": result.get("audit_id"),
        "run_id": result.get("run_id"),
        "last_audited_at": result.get("created_at"),
        "requires_reaudit": False,
    }


def execute(
    *, supabase: SupabaseNamespacedPort, product_ids: list[str], shop_domain: str,
    view: str = "summary",
) -> list[dict[str, Any]]:
    results = supabase.intelligence.list_product_intelligence_product_results(
        product_ids=product_ids, shop_domain=shop_domain,
    )
    result_by_product = {str(row.get("product_id")): row for row in results}
    output: list[dict[str, Any]] = []
    for product_id in product_ids:
        result = result_by_product.get(product_id)
        audit_id = str(result.get("audit_id") or "") if result else ""
        suggestions = (
            supabase.intelligence.list_product_intelligence_suggestions(
                audit_id=audit_id, shop_domain=shop_domain,
            ) if audit_id else []
        )
        suggestions = [row for row in suggestions if str(row.get("product_id") or "") == product_id]
        pending = [row for row in suggestions if row.get("status") == "pending"]
        applied = [row for row in suggestions if row.get("status") == "applied"]
        reverted = [row for row in suggestions if row.get("status") == "reverted"]
        pending_fields = sum(
            len(row.get("patch_payload") or {})
            for row in pending if isinstance(row.get("patch_payload"), dict)
        )
        item: dict[str, Any] = {
            "product_id": product_id,
            "health": _health(result),
            "counts": {
                "pending_suggestions": len(pending), "pending_fields": pending_fields,
                "applied": len(applied), "reverted": len(reverted),
            },
        }
        if view == "review":
            audit = (
                supabase.intelligence.get_product_intelligence_audit(audit_id, shop_domain=shop_domain)
                if audit_id else None
            )
            findings = audit.get("findings") if isinstance(audit, dict) else []
            item["findings"] = [
                row for row in (findings or [])
                if str(row.get("product_id") or "") == product_id
            ]
            item["pending_reviews"] = pending
            item["history"] = sorted(
                [*applied, *reverted],
                key=lambda row: str(row.get("applied_at") or row.get("created_at") or ""),
                reverse=True,
            )
        output.append(item)
    return output

