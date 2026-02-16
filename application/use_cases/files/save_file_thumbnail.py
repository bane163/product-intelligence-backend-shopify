"""Use-case: save file thumbnail via supabase port."""
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, file_id: str, content: bytes) -> str | None:
    return supabase.save_file_thumbnail(file_id=file_id, content=content)
