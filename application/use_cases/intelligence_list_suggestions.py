from application.ports.supabase_port import SupabasePort


def execute(*, supabase: SupabasePort, audit_id: str) -> list[dict]:
    return supabase.list_product_intelligence_suggestions(audit_id=audit_id)
