"""Use-case: delete a product draft via supabase port."""
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, draft_id: str) -> bool:
    return supabase.delete_product_draft(draft_id)
