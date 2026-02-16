"""Use-case: get file thumbnail via supabase port."""
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, file_id: str) -> bytes | None:
    return supabase.get_file_thumbnail(file_id)
