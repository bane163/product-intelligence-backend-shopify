"""Use-case: delete a file via supabase port."""
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, file_id: str) -> bool:
    return supabase.delete_file(file_id)
