"""Use-case: get a product draft via supabase port."""
from typing import Any
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, draft_id: str) -> dict[str, Any] | None:
    return supabase.get_product_draft(draft_id)
