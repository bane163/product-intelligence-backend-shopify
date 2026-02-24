"""Use-case: save file thumbnail via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, file_id: str, content: bytes) -> str | None:
    return supabase.file.save_file_thumbnail(file_id=file_id, content=content)
