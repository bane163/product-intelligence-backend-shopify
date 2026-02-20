from application.ports.supabase_port import SupabasePort


def execute(
    *,
    supabase: SupabasePort,
    shop_domain: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    return supabase.list_product_intelligence_audits(
        shop_domain=shop_domain,
        limit=limit,
        offset=offset,
    )
