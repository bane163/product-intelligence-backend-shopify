from application.ports.supabase_port import SupabasePort


def execute(*, supabase: SupabasePort, limit: int = 50, offset: int = 0) -> list[dict]:
    return supabase.list_product_intelligence_audits(limit=limit, offset=offset)
