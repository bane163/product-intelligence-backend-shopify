from application.ports.supabase_port import SupabaseNamespacedPort


def execute(
    *,
    supabase: SupabaseNamespacedPort,
    shop_domain: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    return supabase.intelligence.list_product_intelligence_audits(
        shop_domain=shop_domain,
        limit=limit,
        offset=offset,
    )
