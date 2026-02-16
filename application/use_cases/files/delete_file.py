"""Use-case: delete a file via supabase port."""
from application.ports.supabase_port import SupabasePort


def execute(supabase: SupabasePort, file_id: str) -> bool:
    return supabase.delete_file(file_id)
