"""Use-case: get file thumbnail via supabase port."""
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, file_id: str) -> bytes | None:
    return supabase.get_file_thumbnail(file_id)
