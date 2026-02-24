"""Use-case: delete a file via supabase port."""
from application.ports.supabase_port import SupabaseNamespacedPort


def execute(supabase: SupabaseNamespacedPort, file_id: str) -> bool:
    return supabase.file.delete_file(file_id)
