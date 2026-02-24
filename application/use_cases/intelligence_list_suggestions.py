from application.ports.supabase_port import SupabaseNamespacedPort


def execute(*, supabase: SupabaseNamespacedPort, audit_id: str, shop_domain: str) -> list[dict]:
    return supabase.intelligence.list_product_intelligence_suggestions(
        audit_id=audit_id,
        shop_domain=shop_domain,
    )
