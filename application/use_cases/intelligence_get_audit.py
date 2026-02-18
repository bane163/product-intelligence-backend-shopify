from typing import Any

from application.ports.supabase_port import SupabasePort


def execute(*, supabase: SupabasePort, audit_id: str) -> dict[str, Any] | None:
    return supabase.get_product_intelligence_audit(audit_id)
