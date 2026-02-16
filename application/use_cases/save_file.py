"""Use-case: save a file via supabase port."""
from services.interfaces import SupabaseServiceInterface


def execute(supabase: SupabaseServiceInterface, file_id: str, name: str, content: bytes, content_type: str | None = None) -> None:
    return supabase.save_file(file_id, name=name, content=content, content_type=content_type)
