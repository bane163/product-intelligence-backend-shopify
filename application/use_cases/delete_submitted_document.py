"""Use-case: delete a submitted document via supabase port."""
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, submitted_id: str) -> bool:
    return supabase.delete_submitted_document(submitted_id)
