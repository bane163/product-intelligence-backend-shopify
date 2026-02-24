from typing import Any

from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    *,
    supabase: SupabaseNamespacedPort,
    audit_id: str,
    shop_domain: str,
) -> dict[str, Any] | None:
    return supabase.intelligence.get_product_intelligence_audit(audit_id, shop_domain=shop_domain)
