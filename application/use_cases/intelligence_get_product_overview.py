from __future__ import annotations

from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort


def _health(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {
            "state": "not_audited",
            "score": None,
            "component_scores": {},
            "findings_count": 0,
            "audit_id": None,
            "run_id": None,
            "last_audited_at": None,
            "requires_reaudit": True,
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
    *,
    supabase: SupabaseNamespacedPort,
    product_ids: list[str],
    shop_domain: str,
    view: str = "summary",
) -> list[dict[str, Any]]:
    results = supabase.intelligence.list_product_intelligence_product_results(
        product_ids=product_ids,
        shop_domain=shop_domain,
    )
    result_by_product = {str(row.get("product_id")): row for row in results}
    audits = [] if view == "review" else supabase.intelligence.list_product_intelligence_audits(
        shop_domain=shop_domain,
        limit=max(50, len(product_ids) * 3),
        offset=0,
    )
    audit_by_id = {str(row.get("audit_id") or ""): row for row in audits}
    output: list[dict[str, Any]] = []
    for product_id in product_ids:
        result = result_by_product.get(product_id)
        audit_id = str(result.get("audit_id") or "") if result else ""
        latest_suggestions = (
            supabase.intelligence.list_product_intelligence_suggestions(
                audit_id=audit_id,
                shop_domain=shop_domain,
            )
            if audit_id
            else []
        )
        latest_suggestions = [
            row
            for row in latest_suggestions
            if str(row.get("product_id") or "") == product_id
        ]
        suggestions = (
            supabase.intelligence.list_product_intelligence_product_suggestions(
                product_id=product_id, shop_domain=shop_domain,
            )
            if view == "review"
            else latest_suggestions
        )
        pending = [row for row in suggestions if row.get("status") == "pending"]
        applied = [row for row in suggestions if row.get("status") == "applied"]
        reverted = [row for row in suggestions if row.get("status") == "reverted"]
        pending_fields = sum(
            len(row.get("patch_payload") or {})
            for row in pending
            if isinstance(row.get("patch_payload"), dict)
        )
        health = _health(result)
        health["requires_reaudit"] = any(row.get("status") in {"applied", "reverted"} for row in latest_suggestions) if result else True
        audit = audit_by_id.get(audit_id)
        if view == "review" and audit_id:
            audit = supabase.intelligence.get_product_intelligence_audit(
                audit_id,
                shop_domain=shop_domain,
            )
        health["enhancement_status"] = (
            audit.get("enhancement_status") if isinstance(audit, dict) else None
        ) or "not_audited"
        snapshot = result.get("audited_snapshot") if isinstance(result, dict) else None
        product_title = (
            result.get("product_title") if isinstance(result, dict) else None
        ) or (snapshot.get("title") if isinstance(snapshot, dict) else None)
        item: dict[str, Any] = {
            "product_id": product_id,
            "product_title": str(product_title or ""),
            "health": health,
            "counts": {
                "pending_suggestions": len(pending),
                "pending_fields": pending_fields,
                "applied": len(applied),
                "reverted": len(reverted),
            },
        }
        if view == "review":
            findings = audit.get("findings") if isinstance(audit, dict) else []
            item["findings"] = [
                row
                for row in (findings or [])
                if str(row.get("product_id") or "") == product_id
            ]
            item["pending_reviews"] = pending
            item["enhanced_changes"] = sorted(
                suggestions,
                key=lambda row: str(row.get("created_at") or ""),
                reverse=True,
            )
            item["history"] = sorted(
                [*applied, *reverted],
                key=lambda row: str(
                    row.get("applied_at") or row.get("created_at") or ""
                ),
                reverse=True,
            )
        output.append(item)
    return output
