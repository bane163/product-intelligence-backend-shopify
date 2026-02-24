"""Use-case: get file thumbnail via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, file_id: str) -> bytes | None:
    return supabase.file.get_file_thumbnail(file_id)
