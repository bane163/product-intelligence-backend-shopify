from application.ports.supabase_port import SupabasePort


def execute(*, supabase: SupabasePort, audit_id: str, shop_domain: str) -> list[dict]:
    return supabase.list_product_intelligence_suggestions(
        audit_id=audit_id,
        shop_domain=shop_domain,
    )
