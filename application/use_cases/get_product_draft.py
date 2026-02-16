"""Use-case: get a product draft via supabase port."""
from typing import Any
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, draft_id: str) -> dict[str, Any] | None:
    return supabase.get_product_draft(draft_id)
