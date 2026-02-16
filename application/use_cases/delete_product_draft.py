"""Use-case: delete a product draft via supabase port."""
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, draft_id: str) -> bool:
    return supabase.delete_product_draft(draft_id)
