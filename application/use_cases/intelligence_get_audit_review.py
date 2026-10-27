from __future__ import annotations

from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort


def _product_index(row: dict[str, Any]) -> int | None:
    raw = row.get("product_index")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        index = int(raw)
    except (TypeError, ValueError):
        return None
    return index if index >= 0 else None


def _belongs_to_product(
    row: dict[str, Any], *, product_index: int, product_id: str
) -> bool:
    row_index = _product_index(row)
    return (row_index is not None and row_index == product_index) or bool(
        product_id and str(row.get("product_id") or "") == product_id
    )


def execute(
    *,
    supabase: SupabaseNamespacedPort,
    audit_id: str,
    shop_domain: str,
) -> dict[str, Any] | None:
    """Build an immutable, always-readable merchant review for one audit."""

    audit = supabase.intelligence.get_product_intelligence_audit(
        audit_id,
        shop_domain=shop_domain,
    )
    if not isinstance(audit, dict):
        return None
    if str(audit.get("status") or "") not in {
        "success",
        "succeeded",
        "needs_attention",
    }:
        return {
            "audit": audit,
            "review_required": True,
            "products": [],
            "enhancement": {
                "status": audit.get("enhancement_status") or "legacy_unknown",
                "error_code": audit.get("enhancement_error_code"),
                "error_message": audit.get("enhancement_error_message"),
                "attempts": int(audit.get("enhancement_attempts") or 0),
                "ai_suggestions_count": int(audit.get("ai_suggestions_count") or 0),
                "derived_suggestions_count": int(
                    audit.get("derived_suggestions_count") or 0
                ),
            },
        }

    suggestions = supabase.intelligence.list_product_intelligence_suggestions(
        audit_id=audit_id,
        shop_domain=shop_domain,
    )
    findings = [item for item in audit.get("findings", []) if isinstance(item, dict)]
    totals = audit.get("totals") if isinstance(audit.get("totals"), dict) else {}
    snapshots = [
        item for item in totals.get("audited_products", []) if isinstance(item, dict)
    ]
    product_indexes = set(range(len(snapshots)))
    for row in [*findings, *suggestions]:
        index = _product_index(row)
        if index is not None:
            product_indexes.add(index)

    products: list[dict[str, Any]] = []
    for index in sorted(product_indexes):
        snapshot = snapshots[index] if 0 <= index < len(snapshots) else {}
        product_id = str(snapshot.get("id") or "")
        product_findings = [
            row
            for row in findings
            if _belongs_to_product(row, product_index=index, product_id=product_id)
        ]
        product_suggestions = [
            row
            for row in suggestions
            if _belongs_to_product(row, product_index=index, product_id=product_id)
        ]
        products.append(
            {
                "product_index": index,
                "product_id": product_id
                or next(
                    (
                        str(row.get("product_id") or "")
                        for row in product_suggestions
                        if row.get("product_id")
                    ),
                    "",
                ),
                "product_title": str(
                    snapshot.get("title")
                    or next(
                        (
                            row.get("product_title")
                            for row in [*product_suggestions, *product_findings]
                            if row.get("product_title")
                        ),
                        f"Product #{index + 1}",
                    )
                ),
                "audited_snapshot": snapshot,
                "findings": product_findings,
                "enhanced_changes": product_suggestions,
                "pending_reviews": [
                    row for row in product_suggestions if row.get("status") == "pending"
                ],
                "history": [
                    row for row in product_suggestions if row.get("status") != "pending"
                ],
            }
        )

    return {
        "audit": audit,
        "review_required": True,
        "enhancement": {
            "status": audit.get("enhancement_status") or "legacy_unknown",
            "error_code": audit.get("enhancement_error_code"),
            "error_message": audit.get("enhancement_error_message"),
            "attempts": int(audit.get("enhancement_attempts") or 0),
            "ai_suggestions_count": int(audit.get("ai_suggestions_count") or 0),
            "derived_suggestions_count": int(
                audit.get("derived_suggestions_count") or 0
            ),
        },
        "products": products,
    }
